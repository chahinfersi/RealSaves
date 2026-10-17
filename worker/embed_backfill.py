"""Give `ready` reels their vector for vector search — and re-embed them if the model changed.

Selects every `ready` row whose `embeddingModel` is not the current model (that includes rows
with no embedding at all), embeds caption + note + transcript, and $sets three fields:
`embedding`, `embeddingModel`, `embeddedAt`. Nothing else is touched. Runs locally, never
contacts Instagram/TikTok/Facebook. Running it twice is harmless: done rows aren't selected.

Run it (from the project root):

    python worker/embed_backfill.py --dry-run    # count what would be embedded, write nothing
    python worker/embed_backfill.py --limit 4    # try a few first
    python worker/embed_backfill.py              # do them all (~2 s per reel on CPU)
"""

import argparse
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import db
import vectors

BATCH = 8  # reels embedded per model call, then written


def main():
    parser = argparse.ArgumentParser(description="Embed ready reels for vector search.")
    parser.add_argument("--dry-run", action="store_true", help="only count, write nothing")
    parser.add_argument("--limit", type=int, default=None, help="max rows to embed")
    args = parser.parse_args()

    collection = db.get_collection()
    model_id = vectors.current_model_id()
    query = {"status": "ready", "embeddingModel": {"$ne": model_id}}
    # Skip the existing vectors when reading: we're about to replace them anyway.
    docs = list(collection.find(query, {"embedding": 0}).sort("capturedAt", 1))
    if args.limit is not None:
        docs = docs[: args.limit]
    print(f"[embed] model {model_id}: {len(docs)} ready reel(s) to embed")
    if args.dry_run or not docs:
        return

    t0 = time.perf_counter()
    for start in range(0, len(docs), BATCH):
        batch = docs[start:start + BATCH]
        for doc, fields in zip(batch, vectors.embedding_fields(batch)):
            collection.update_one({"_id": doc["_id"]}, {"$set": fields})
        print(f"[embed] {start + len(batch)}/{len(docs)}  ({time.perf_counter() - t0:.0f}s)")

    print(f"[embed] done. {len(docs)} reel(s) embedded in {time.perf_counter() - t0:.0f}s")


if __name__ == "__main__":
    main()
