# The MCP Server, Explained (with diagrams)

A visual companion for reading the code. Pair it with [`server.py`](server.py) and
[`db.py`](db.py). For the concepts (what MCP is, tools vs resources, pull vs push) see
[`../PHASE3-EXPLAINED.md`](../PHASE3-EXPLAINED.md).

> Mental model: the server is a **librarian at a desk**. Claude walks up with a request slip
> (`tools/call`). The librarian checks the catalogue (MongoDB), and answers in one of two
> ways: a **short list of index cards** (`list_new_reels`) or **one whole book**
> (`get_reel`). The librarian never leaves the desk and never starts a conversation.

---

## 1. The file map — who calls whom

```mermaid
flowchart TD
    subgraph host["Claude Desktop (the host)"]
        CL["🧠 Claude"]
    end
    subgraph ours["our code (mcp_server/)"]
        S["server.py<br/>tools + output models"]
        DB["db.py<br/>connection + owner"]
    end
    CL <-->|"JSON-RPC over stdio<br/>(stdin / stdout)"| S
    S -->|"get_collection()<br/>get_owner()"| DB
    DB -->|"pymongo"| M[("MongoDB Atlas<br/>saves collection")]
    ENV[".env<br/>MONGODB_URI, DB_NAME"] -.-> DB
    CFG["claude_desktop_config.json<br/>env: REALSAVES_OWNER"] -.-> DB

    style S fill:#1d3557,color:#fff
    style M fill:#003049,color:#fff
```

**Read it as:** Claude only ever talks to `server.py`. `server.py` only asks `db.py` for two
things (the collection and the owner). Settings come from two places: the database address
from `.env` (shared with the API and worker), and **whose reels** from the Claude Desktop
config (it belongs to the client connection).

---

## 2. Startup — what happens when Claude Desktop launches

```mermaid
sequenceDiagram
    participant CD as Claude Desktop
    participant S as server.py
    participant DB as db.py

    CD->>S: starts python.exe server.py<br/>(with env REALSAVES_OWNER)
    S->>DB: import db → loads .env (file-relative path)
    S->>DB: get_owner()
    DB-->>S: "alice"  (or RuntimeError → server exits)
    S->>S: register tools: docstrings → descriptions,<br/>type hints → input schemas,<br/>return models → output schemas
    S-->>CD: stderr: "starting (stdio) for owner='alice'"
    CD->>S: initialize + tools/list
    S-->>CD: 2 tools (list_new_reels, get_reel)
```

Note: **the Mongo connection is lazy.** Nothing talks to Atlas until the first tool call
(`get_collection()` opens the client then, and keeps it for the server's life).

---

## 3. One conversation — list, then detail

```mermaid
sequenceDiagram
    participant CL as Claude
    participant S as server.py
    participant M as MongoDB

    CL->>S: tools/call list_new_reels {limit: 10}
    S->>M: count_documents(owner, ready, unread)
    M-->>S: 24
    S->>M: find(same filter).sort(capturedAt desc).limit(10)
    M-->>S: 10 documents
    S->>S: _summarize() each → ReelSummary
    S-->>CL: {total_unread: 24, reels: [10 × ~110 tokens]}

    Note over CL: Claude picks the relevant one

    CL->>S: tools/call get_reel {id: "66f…", include_segments: false}
    S->>M: find_one(_id, owner, ready)
    M-->>S: 1 document
    S-->>CL: Reel {transcript, url, metadata…}
```

**The pattern:** the list is cheap (~1,100 tokens for 10 reels), the detail is expensive
(a full transcript), and Claude only pays for detail when it needs it.

---

## 4. How the data changes shape

```mermaid
flowchart LR
    A["Mongo document<br/>_id: ObjectId<br/>capturedAt: datetime<br/>caption: up to ~700 chars<br/>transcript, segments[106]…"]
      -->|"_summarize()"| B["ReelSummary<br/>id: str<br/>capturedAt: ISO str<br/>caption: ≤150 chars<br/>gist: first 200 chars"]
    B -->|"SDK (pydantic)"| C["JSON on stdout<br/>text content +<br/>structured content"]

    style A fill:#780000,color:#fff
    style C fill:#2d6a4f,color:#fff
```

Three conversions to notice in the code:

| Mongo gives | JSON needs | Where |
|---|---|---|
| `ObjectId("66f…")` | `"66f…"` (string) | `str(doc["_id"])` |
| `datetime` | ISO string | `_iso()` |
| a long caption with newlines | one short line | `_truncate()` |
| `caption` (or only `title` on rows saved before the caption fix) | the best caption available | `_caption()` |

### Why pydantic models, not plain dicts

Because the tools return `ReelList` / `Reel` models, the SDK:
1. publishes an **output schema** for each tool (Claude and programs know the exact shape), and
2. sends **structured content** (clean JSON) next to the text version.

The `Field(description=...)` texts end up in those schemas: they document the data *for Claude*.

---

## 5. Input schemas — from type hints

```
Python:  limit: Annotated[int, Field(ge=1, le=50, description="How many reels to return")] = 10
                     │
                     ▼  (pydantic)
JSON:    "limit": {"type": "integer", "minimum": 1, "maximum": 50,
                   "default": 10, "description": "How many reels to return"}
```

`ge`/`le` mean the SDK **rejects** `limit: 500` before our code even runs. Validation for free.

---

## 6. The error paths

```mermaid
flowchart TD
    G["get_reel(id)"] --> V{"valid ObjectId?"}
    V -->|no| E1["raise ToolError<br/>'not a valid reel id'"]
    V -->|yes| F{"found for this owner<br/>and ready?"}
    F -->|no| E2["raise ToolError<br/>'No transcribed reel…'"]
    F -->|yes| OK["return Reel"]
    E1 --> R["Claude gets is_error=true<br/>+ OUR message → can recover"]
    E2 --> R
    X["any other exception<br/>(bug, Atlas down)"] --> C["SDK treats it as a crash<br/>Claude only sees<br/>'Error executing tool get_reel'"]

    style R fill:#2d6a4f,color:#fff
    style C fill:#780000,color:#fff
```

**Key idea (SDK v2):** `ToolError` = *"a failure I saw coming"*: Claude reads our exact
message and can fix its call (e.g. use an id from the list). Any other exception = *crash*:
the details are hidden from Claude on purpose (they could leak internals) and logged to
stderr with a traceback. The server keeps running either way.

**Owner safety:** every query filters by `owner`, so another user's reel id behaves exactly
like a non-existent one ("not found"), and nothing leaks.

---

## 7. Quick function reference

| File | Function | Takes in | Gives back |
|---|---|---|---|
| db.py | `get_owner()` | — (env `REALSAVES_OWNER`) | owner string, or raises |
| db.py | `get_collection()` | — (reads .env) | the `saves` collection (cached client) |
| server.py | `list_new_reels(limit)` | 1–50 | `ReelList {total_unread, reels}` |
| server.py | `get_reel(id, include_segments)` | reel id + flag | `Reel` (or `ToolError`) |
| server.py | `_summarize(doc)` | a Mongo document | `ReelSummary` |
| server.py | `_truncate(text, n)` / `_iso(v)` | text / datetime | short one-liner / ISO string |
| server.py | `log(msg)` | a message | — (writes to **stderr**) |

> Tip while reading the code: start at the bottom (`if __name__ == "__main__"`), then read
> the two `@mcp.tool` functions. The models above them are just the *shapes* those tools return.
