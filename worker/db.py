"""MongoDB access for the worker.

This is the Python counterpart of the Node `lib/db.js` — it connects to the SAME
database the ingest API writes to (MONGODB_URI + DB_NAME from the project-root .env)
and the SAME `saves` collection. We don't reuse the Node file because the worker is a
separate Python process; the logic here is deliberately small.

The one non-obvious piece is `claim_next`: it atomically flips one `pending` row to
`processing` so that two worker runs (or a crash-and-retry) never grab the same reel.
"""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient, ReturnDocument

# Load the project-root .env (one level up from this worker/ folder), so the worker
# reads exactly the same MONGODB_URI / DB_NAME as the rest of the app.
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH)

COLLECTION = "saves"

# A reel that has been claimed and failed this many times is given up on (marked failed),
# so one "poison" reel can never be retried forever and block the queue.
MAX_ATTEMPTS = 3

_client = None  # reuse a single client across the run
_indexes_ensured = False  # createIndex runs at most once per process


def get_collection():
    """Return the `saves` collection, opening (and caching) the client on first use."""
    global _client
    uri = os.environ.get("MONGODB_URI")
    db_name = os.environ.get("DB_NAME")
    if not uri:
        raise RuntimeError("MONGODB_URI is not set (check .env)")
    if not db_name:
        raise RuntimeError("DB_NAME is not set (check .env)")
    if _client is None:
        _client = MongoClient(uri)
    collection = _client[db_name][COLLECTION]
    ensure_indexes(collection)
    return collection


def ensure_indexes(collection):
    """Create the index the worker's claim query needs, once per process.

    The worker constantly asks "the oldest row where status == pending". Without an
    index, MongoDB must scan EVERY document to find the few pending ones — fine at 20
    rows, slow at 50,000. This index makes that lookup instant no matter how big the
    collection grows.

    It's a PARTIAL index (only rows where status == "pending" are indexed), so:
      - the index stays tiny — it only ever holds the unprocessed backlog;
      - a reel drops out of the index automatically the moment it becomes ready/failed.
    createIndex is idempotent, so calling this repeatedly is safe.
    """
    global _indexes_ensured
    if _indexes_ensured:
        return
    collection.create_index(
        [("status", 1), ("capturedAt", 1)],
        name="pending_capturedAt",
        partialFilterExpression={"status": "pending"},
    )
    _indexes_ensured = True


def claim_next(collection):
    """Atomically claim the oldest `pending` row: flip it to `processing` and return it.

    Using find_one_and_update means the read + the state flip happen as ONE atomic op,
    so concurrent runs can never both claim the same document. Returns None when there
    is nothing left to do. `attempts` is incremented so we can later cap retries.
    """
    now = datetime.now(timezone.utc)
    return collection.find_one_and_update(
        {"status": "pending"},
        {
            "$set": {"status": "processing", "processingStartedAt": now},
            "$inc": {"attempts": 1},
        },
        sort=[("capturedAt", 1)],  # oldest first (FIFO)
        return_document=ReturnDocument.AFTER,
    )


def mark_ready(collection, _id, fields):
    """Write the transcript + metadata and flip the row to `ready`."""
    fields = dict(fields)
    fields["status"] = "ready"
    fields["processedAt"] = datetime.now(timezone.utc)
    fields["error"] = None
    collection.update_one({"_id": _id}, {"$set": fields})


def mark_failed(collection, _id, error):
    """Flip a row to `failed` with a human-readable reason (login wall, blocked, etc.)."""
    collection.update_one(
        {"_id": _id},
        {
            "$set": {
                "status": "failed",
                "error": str(error)[:500],
                "processedAt": datetime.now(timezone.utc),
            }
        },
    )


def reclaim_stale(collection, older_than_minutes=15):
    """Recover reels orphaned in `processing` by a crash (dead battery, kill, etc.).

    If the worker dies mid-job, the row it had claimed is left stuck in `processing` and
    would never be picked up again (claim_next only looks at `pending`). This runs at the
    start of each worker run and, for any row stuck in `processing` longer than the
    threshold (no real job takes 15 min), either:
      - resets it to `pending` so it gets retried, OR
      - if it has already used up MAX_ATTEMPTS, marks it `failed` so a reel that keeps
        crashing the process can't loop forever.

    The threshold must be comfortably longer than a normal job so we never yank a reel
    that's legitimately still being transcribed. Returns (requeued, given_up) counts.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)
    stale = {"status": "processing", "processingStartedAt": {"$lt": cutoff}}

    given_up = collection.update_many(
        {**stale, "attempts": {"$gte": MAX_ATTEMPTS}},
        {
            "$set": {
                "status": "failed",
                "error": f"gave up after {MAX_ATTEMPTS} attempts (kept crashing/stalling)",
                "processedAt": datetime.now(timezone.utc),
            }
        },
    ).modified_count

    requeued = collection.update_many(
        {**stale, "attempts": {"$lt": MAX_ATTEMPTS}},
        {"$set": {"status": "pending"}},
    ).modified_count

    return requeued, given_up
