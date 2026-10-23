# RealSaves MCP Server (Phase 3)

The local server that lets Claude read your saved reels. It runs on your machine, and
Claude Desktop starts it as a child process and talks to it over **stdio**. See
[`../PHASE3-EXPLAINED.md`](../PHASE3-EXPLAINED.md) for the concepts,
[`EXPLAINED.md`](EXPLAINED.md) for how this code works (with diagrams), and
[`../ARCHITECTURE.md`](../ARCHITECTURE.md) §9 for where it fits.

> **Status: vector search.** Three read-only tools over the `saves` collection. Claude finds
> reels related to the task you start a chat with (`find_reels`), asks you, then reads them
> (`get_reel`) and applies their ideas. This is RAG: see
> [`../VECTOR-SEARCH-EXPLAINED.md`](../VECTOR-SEARCH-EXPLAINED.md).

## Tools

| Tool | Input | Returns |
|---|---|---|
| `find_reels` | `topic` (one focused sentence), `limit` (1–10, default 5) | reels related to the topic, by meaning and in any language, with a `similarity` score; only those above the threshold (0.50), so it can be empty |
| `list_new_reels` | `limit` (1–50, default 10) | `total_unread` + short summaries of the newest unread `ready` reels |
| `get_reel` | `id`, `include_segments` (default false) | one reel in full: transcript, metadata, link (+ timestamped lines if asked) |

`find_reels` embeds the topic with the same local model as the worker
([`../shared/embedding.py`](../shared/embedding.py)), then runs an Atlas `$vectorSearch`
pre-filtered on owner, `status: "ready"` and the embedding model. All three are marked **read-only** (`readOnlyHint`) and only ever see the reels of one owner.

## Setup (one time)

Requires **Python 3.10+** (we use the same 3.12 as the worker).

```bash
python -m pip install -r mcp_server/requirements.txt
```

`find_reels` also needs, once (details in [`../shared/README.md`](../shared/README.md)):
reels embedded (`python worker/embed_backfill.py`) and the Atlas vector index `reels_vector`
(`python shared/create_vector_index.py`, or the Atlas UI). The server loads the embedding
model (bge-m3, ~2.3 GB, downloaded on first use) in the background at startup, ~5 s.

`MONGODB_URI` and `DB_NAME` come from the project-root `.env` (same as the API and worker).
**Whose reels** comes from the `REALSAVES_OWNER` env var: the server refuses to start
without it. Its value is your owner name, the part before `:` in your `API_KEYS` entry
(`<owner>:<key>`, see [`../api/README.md`](../api/README.md)).

In the commands below, replace the placeholders with your own values:

| Placeholder | What it is | How to find it |
|---|---|---|
| `<owner>` | your owner name | the `owner` part of your `API_KEYS` entry |
| `<python.exe>` | full path to your Python | `python -c "import sys; print(sys.executable)"` |
| `<repo>` | full path to this repo | the folder you cloned it into |

## 1. Run it directly (and see that it "does nothing")

```powershell
$env:REALSAVES_OWNER = "<owner>"; python mcp_server/server.py
```

It prints one line to stderr, then **sits silently**: a stdio server waits for JSON
messages on stdin from a client. Press `Ctrl+C` to stop it (the traceback is normal).

## 2. Test it with the MCP Inspector

The Inspector is a small web UI that acts as an MCP client, so you can see the raw
messages. `-e` passes the env var to the server:

```bash
npx @modelcontextprotocol/inspector -e REALSAVES_OWNER=<owner> "<python.exe>" mcp_server/server.py
```

Connect → **Tools** → list them, run them, and read the JSON in the **Protocol** panel.
Your `log()` lines show up in the **Console** tab.

## 3. Connect it to Claude Desktop

Open **Claude Desktop → Settings → Developer → Edit Config**; the `realsaves` entry is:

```json
{
  "mcpServers": {
    "realsaves": {
      "command": "<python.exe>",
      "args": ["<repo>\\mcp_server\\server.py"],
      "env": { "REALSAVES_OWNER": "<owner>" }
    }
  }
}
```

For example, `"command": "C:\\Users\\you\\AppData\\Local\\Programs\\Python\\Python312\\python.exe"`.
In JSON every backslash is written **twice** (`\\`), because `\` starts an escape sequence.

(If the file already has other keys, add `mcpServers` next to them; don't replace the file.)

- **Absolute paths on purpose:** Claude Desktop doesn't start in this repo folder, and the
  full Python path avoids the Microsoft Store `python` stub (same reason as
  `worker/run-worker.ps1`).
- **`env`:** extra environment variables Claude Desktop gives the server process. That's
  how the server knows whose reels to serve.
- After **any** change (config *or* code), **fully quit** Claude Desktop (tray icon → Quit)
  and reopen: it starts the server once, at launch.

## 4. Make Claude search your reels by itself

An MCP server can't speak first: Claude only calls `find_reels` when something tells it to.
Paste this once in **Claude Desktop → Settings → Profile → Instructions for Claude**
(called "personal preferences" in older versions). It applies to every chat:

> When my first message in a chat describes a work task or project (for example "today: SEO
> for our landing page"), call the realsaves `find_reels` tool with one focused search per
> sub-topic. If it returns reels that really fit, tell me briefly how many and what they're
> about, and ask whether I want to use them. If I say yes, read them with `get_reel`, but
> don't show me the transcripts or recap them: extract their core ideas and techniques and
> apply them directly to my task, mentioning in a few words which reel each idea comes from.
> If none fit, don't mention reels. Don't do this for greetings or casual chat.

This only covers Claude Desktop chats. Claude Code has its own MCP config (`claude mcp add`)
and reads instructions from `CLAUDE.md`.

## Logs

Anything the server writes to **stderr** is captured by Claude Desktop. To read it:
**Settings → Developer → realsaves → Show logs**. The file itself is
`mcp-server-realsaves.log`, in `%LOCALAPPDATA%\Claude\logs\` (current builds). Every
`find_reels` call logs its query and how many reels passed the threshold, handy to see what
Claude actually searched for.

While developing, the **Inspector's Console tab** shows the same stderr lines live.

> ⚠️ **Never `print()` to stdout in this server.** With stdio, stdout *is* the protocol
> channel; a stray print corrupts it and Claude shows a connection error. Use the `log()`
> helper in `server.py` (it writes to stderr).
