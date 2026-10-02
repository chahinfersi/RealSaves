"""One-off (re-runnable) backfill: give already-`ready` reels the fields newer worker
versions store, without re-transcribing anything.

Fills, for every `ready` row that lacks them:
  - `caption`     → yt-dlp metadata only (no download): the post's real caption
  - `durationSec` → from that metadata if the platform reports it; otherwise (Instagram)
                    download the audio and measure its length. No transcription.

Safe by design: it only ADDS these two fields. It never touches `status`, `transcript`
or anything else, so a reel that has since been deleted/made private just gets skipped
with a log line, never turned from `ready` into `failed`. Rows already complete are not
selected, so running it twice is harmless (idempotent).

Run it (from the project root):

    python worker/backfill.py --dry-run    # list what would change, no network calls
    python worker/backfill.py --limit 2    # try a couple first
    python worker/backfill.py              # do them all
"""

import argparse
import os
import random
import shutil
import sys
import tempfile
import time

# Windows consoles default to cp1252 and crash on non-ASCII captions; force UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from faster_whisper import decode_audio

import db
import resolve

# `ready` rows missing either field. ($in [None] also matches a field that doesn't exist.)
NEEDS_BACKFILL = {
    "status": "ready",
    "$or": [{"caption": {"$in": [None, ""]}}, {"durationSec": None}],
}

SAMPLE_RATE = 16000  # decode_audio resamples to 16 kHz mono, so samples / 16000 = seconds


def audio_length_seconds(url, tmp_dir):
    """Download the audio (as the worker does) and measure its length. No transcription."""
    path = resolve.download_audio(url, tmp_dir)
    try:
        return round(len(decode_audio(path, sampling_rate=SAMPLE_RATE)) / SAMPLE_RATE)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def backfill_one(collection, doc, tmp_dir):
    """Compute the missing fields for one row and $set them. Returns the dict written."""
    url = doc.get("url") or doc.get("rawUrl")
    fields = {}

    if not doc.get("caption"):
        meta = resolve.extract_info(url)  # metadata only, no download
        if meta["caption"]:
            fields["caption"] = meta["caption"]
        if doc.get("durationSec") is None and meta["durationSec"] is not None:
            fields["durationSec"] = meta["durationSec"]

    if doc.get("durationSec") is None and "durationSec" not in fields:
        fields["durationSec"] = audio_length_seconds(url, tmp_dir)

    if fields:
        collection.update_one({"_id": doc["_id"]}, {"$set": fields})
    return fields


def main():
    parser = argparse.ArgumentParser(description="Backfill caption + durationSec on ready reels.")
    parser.add_argument("--dry-run", action="store_true", help="only list what would change")
    parser.add_argument("--limit", type=int, default=None, help="max rows to process")
    args = parser.parse_args()

    collection = db.get_collection()
    docs = list(collection.find(NEEDS_BACKFILL).sort("capturedAt", 1))
    if args.limit is not None:
        docs = docs[: args.limit]
    print(f"[backfill] {len(docs)} ready row(s) need caption and/or durationSec")

    if args.dry_run:
        for d in docs:
            missing = [f for f in ("caption", "durationSec") if d.get(f) in (None, "")]
            print(f"  {d['platform']:<9} {d['_id']}  missing: {', '.join(missing)}")
        return

    tmp_dir = tempfile.mkdtemp(prefix="reelsaves_backfill_")
    done = skipped = 0
    try:
        for i, d in enumerate(docs):
            label = f"{d.get('platform')}:{d.get('contentId')}"
            try:
                fields = backfill_one(collection, d, tmp_dir)
                done += 1
                shown = {k: (v[:60] + "…" if isinstance(v, str) and len(v) > 60 else v)
                         for k, v in fields.items()}
                print(f"[backfill] ✓ {label}  {shown}")
            except Exception as err:  # deleted/private/blocked: leave the row exactly as is
                skipped += 1
                print(f"[backfill] ✗ skipped {label}: {err}")
            # Politeness: small random pause between platform requests (ARCHITECTURE.md §8).
            if i < len(docs) - 1:
                time.sleep(random.uniform(1.0, 2.5))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print(f"[backfill] done. updated={done} skipped={skipped}")


if __name__ == "__main__":
    main()
