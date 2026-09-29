"""RealSaves Phase 2 worker (run-once).

Finishes the job the ingest API deliberately left undone: for every `pending` row it
resolves the real video, downloads audio-only, transcribes it locally with Whisper, and
writes the transcript back with status `ready`. Processes everything waiting, prints a
summary, and exits.

Run it (from the project root):

    python worker/worker.py                 # process all pending rows
    python worker/worker.py --limit 1       # process just one (handy while testing)
    python worker/worker.py --model medium  # bump accuracy if `small` is too rough
    python worker/worker.py --translate     # force English transcripts

Requires ffmpeg on PATH (or set FFMPEG_LOCATION to its bin dir) and the deps in
worker/requirements.txt.
"""

import argparse
import os
import random
import shutil
import sys
import tempfile
import time
from collections import Counter

# Windows consoles default to cp1252 and crash on the status symbols below; force UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from pymongo.errors import DuplicateKeyError

import db
import resolve
import transcribe


def process_one(collection, doc, tmp_dir, model_size, translate):
    """Resolve → reconcile → download → transcribe one claimed row.

    Returns "ready" or "duplicate". Raises resolve.ResolveError (or another exception)
    on failure; the caller records it as `failed`.
    """
    _id = doc["_id"]
    owner = doc.get("owner")
    platform = doc.get("platform")
    url = doc.get("url") or doc.get("rawUrl")

    # 1. Resolve real metadata (no download). Raises ResolveError on a login wall etc.
    meta = resolve.extract_info(url)
    real_id = meta["id"]
    if not real_id:
        raise resolve.ResolveError("resolved metadata had no id")

    meta_fields = {
        "title": meta["title"],
        "uploader": meta["uploader"],
        "durationSec": meta["durationSec"],
        "thumbnailUrl": meta["thumbnailUrl"],
    }

    # 2. Reconcile dedup to the real id (TikTok/Facebook were provisional short codes).
    new_dedup = f"{platform}:{real_id}"
    if new_dedup != doc.get("dedupKey"):
        try:
            collection.update_one(
                {"_id": _id},
                {
                    "$set": {
                        "contentId": real_id,
                        "dedupKey": new_dedup,
                        "resolved": True,
                        "url": meta["webpageUrl"] or url,
                        **meta_fields,
                    }
                },
            )
        except DuplicateKeyError:
            # This owner already has the same real video (shared via two different codes).
            # Keep the existing row, drop this provisional one, carry the note over if the
            # kept row has none — mirrors the duplicate-note logic in api/saves.js.
            existing = collection.find_one(
                {"owner": owner, "dedupKey": new_dedup, "_id": {"$ne": _id}}
            )
            note = doc.get("note")
            if existing and note and not existing.get("note"):
                collection.update_one({"_id": existing["_id"]}, {"$set": {"note": note}})
            collection.delete_one({"_id": _id})
            return "duplicate"
    else:
        # Already canonical at ingest (Instagram): just fill in metadata.
        collection.update_one({"_id": _id}, {"$set": {"resolved": True, **meta_fields}})

    # 3. Download audio-only to a temp file.
    audio_path = resolve.download_audio(url, tmp_dir)
    try:
        # 4. Transcribe.
        result = transcribe.transcribe(audio_path, model_size=model_size, translate=translate)
    finally:
        # 5. Always delete the temp audio, success or not.
        try:
            os.remove(audio_path)
        except OSError:
            pass

    db.mark_ready(
        collection,
        _id,
        {
            "transcript": result["text"],
            "transcriptLang": result["language"],
            "segments": result["segments"],
        },
    )
    return "ready"


def main():
    parser = argparse.ArgumentParser(description="RealSaves Phase 2 worker (run-once).")
    parser.add_argument("--model", default="small", help="Whisper model size (default: small)")
    parser.add_argument("--limit", type=int, default=None, help="max rows to process this run")
    parser.add_argument("--translate", action="store_true", help="force English transcripts")
    parser.add_argument(
        "--reclaim-minutes",
        type=int,
        default=15,
        help="requeue rows stuck in 'processing' longer than this (crash recovery)",
    )
    args = parser.parse_args()

    collection = db.get_collection()
    tmp_dir = tempfile.mkdtemp(prefix="reelsaves_")
    counts = Counter()
    processed = 0

    print(f"[worker] starting (model={args.model}, translate={args.translate})")

    # Crash recovery: before claiming anything, rescue reels a previous run left orphaned
    # in `processing` (e.g. the laptop died mid-job). See db.reclaim_stale.
    requeued, given_up = db.reclaim_stale(collection, args.reclaim_minutes)
    if requeued or given_up:
        print(f"[worker] reclaimed {requeued} stale row(s) → pending, gave up on {given_up}")
    try:
        while True:
            if args.limit is not None and processed >= args.limit:
                break
            doc = db.claim_next(collection)
            if doc is None:
                break
            processed += 1

            label = f"{doc.get('platform')}:{doc.get('contentId')}"
            print(f"[worker] → processing {label}  (attempt {doc.get('attempts')})")
            try:
                outcome = process_one(collection, doc, tmp_dir, args.model, args.translate)
                if outcome == "ready":
                    print(f"[worker] ✓ ready    {label}")
                else:
                    print(f"[worker] ⧉ duplicate {label} (merged into existing)")
            except resolve.ResolveError as err:
                db.mark_failed(collection, doc["_id"], err)
                outcome = "failed"
                print(f"[worker] ✗ failed   {label}: {err}")
            except Exception as err:  # never let one bad row kill the run
                db.mark_failed(collection, doc["_id"], f"unexpected: {err}")
                outcome = "failed"
                print(f"[worker] ✗ failed   {label}: unexpected: {err}")
            counts[outcome] += 1

            # Politeness: small random pause between jobs (ARCHITECTURE.md section 8).
            if args.limit is None or processed < args.limit:
                time.sleep(random.uniform(1.0, 2.5))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    if processed == 0:
        print("[worker] nothing pending — done.")
    else:
        print(
            f"[worker] done. processed={processed} "
            f"ready={counts['ready']} duplicate={counts['duplicate']} failed={counts['failed']}"
        )


if __name__ == "__main__":
    main()
