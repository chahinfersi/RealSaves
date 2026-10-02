"""MongoDB access for the MCP server (read side).

Connects to the SAME database the ingest API and the worker use (MONGODB_URI + DB_NAME from
the project-root .env) and the SAME `saves` collection. We don't import `worker/db.py`: the
two are separate processes, and the worker's `get_collection()` also creates the worker's
own index, which a reader has no business doing. Same precedent as `worker/db.py` being a
small copy of the Node `lib/db.js`.

Whose reels? The server serves ONE owner, set by the REALSAVES_OWNER env var. Claude Desktop
passes it through the `"env"` block of claude_desktop_config.json, so identity belongs to
the client connection, not to the shared .env.
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

# Claude Desktop starts us from ITS folder, not the repo — so resolve .env relative to this
# file (one level up from mcp_server/), never relative to the current working directory.
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH)

COLLECTION = "saves"

_client = None  # one client for the server's whole life (it runs as long as Claude Desktop)


def get_owner():
    """Return the owner whose reels this server serves. Fails loudly if it isn't set."""
    owner = os.environ.get("REALSAVES_OWNER")
    if not owner:
        raise RuntimeError(
            "REALSAVES_OWNER is not set — add it to the realsaves 'env' block in "
            "claude_desktop_config.json (or pass -e REALSAVES_OWNER=... to the Inspector)"
        )
    return owner


def get_collection():
    """Return the `saves` collection, opening (and caching) the client on first use.

    pymongo is synchronous (each query blocks until Atlas answers). That's fine here: one
    user, one conversation, small queries.
    """
    global _client
    uri = os.environ.get("MONGODB_URI")
    db_name = os.environ.get("DB_NAME")
    if not uri:
        raise RuntimeError("MONGODB_URI is not set (check .env)")
    if not db_name:
        raise RuntimeError("DB_NAME is not set (check .env)")
    if _client is None:
        # tz_aware=True: Mongo stores UTC, but pymongo returns timezone-LESS datetimes by
        # default, so the ISO strings we send would lack "+00:00" and could be misread as
        # local time. With it, capturedAt reads e.g. "2026-09-30T23:58:17.788000+00:00".
        _client = MongoClient(uri, tz_aware=True)
    return _client[db_name][COLLECTION]
