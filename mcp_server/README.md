# RealSaves MCP Server (Phase 3)

The local server that lets Claude read your saved reels. It runs on your machine, and
Claude Desktop starts it as a child process and talks to it over **stdio**. See
[`../PHASE3-EXPLAINED.md`](../PHASE3-EXPLAINED.md) for the concepts,
[`EXPLAINED.md`](EXPLAINED.md) for how this code works (with diagrams), and
[`../ARCHITECTURE.md`](../ARCHITECTURE.md) §9 for where it fits.

> **Status: step 2 — first real tools.** Two read-only tools over the `saves` collection.
> Next: `mark_read` + the proactive greeting (step 3).

## Tools

| Tool | Input | Returns |
|---|---|---|
| `list_new_reels` | `limit` (1–50, default 10) | `total_unread` + short summaries of the newest unread `ready` reels |
| `get_reel` | `id`, `include_segments` (default false) | one reel in full: transcript, metadata, link (+ timestamped lines if asked) |

Both are marked **read-only** (`readOnlyHint`) and only ever see the reels of one owner.

## Setup (one time)

Requires **Python 3.10+** (we use the same 3.12 as the worker).

```bash
python -m pip install -r mcp_server/requirements.txt
```

`MONGODB_URI` and `DB_NAME` come from the project-root `.env` (same as the API and worker).
**Whose reels** comes from the `REALSAVES_OWNER` env var: the server refuses to start
without it.

## 1. Run it directly (and see that it "does nothing")

```powershell
$env:REALSAVES_OWNER = "fersi"; python mcp_server/server.py
```

It prints one line to stderr, then **sits silently**: a stdio server waits for JSON
messages on stdin from a client. Press `Ctrl+C` to stop it (the traceback is normal).

## 2. Test it with the MCP Inspector

The Inspector is a small web UI that acts as an MCP client, so you can see the raw
messages. `-e` passes the env var to the server:

```bash
npx @modelcontextprotocol/inspector -e REALSAVES_OWNER=fersi "C:\Users\fersi\AppData\Local\Programs\Python\Python312\python.exe" mcp_server/server.py
```

Connect → **Tools** → list them, run them, and read the JSON in the **Protocol** panel.
Your `log()` lines show up in the **Console** tab.

## 3. Connect it to Claude Desktop

Open **Claude Desktop → Settings → Developer → Edit Config**; the `realsaves` entry is:

```json
{
  "mcpServers": {
    "realsaves": {
      "command": "C:\\Users\\fersi\\AppData\\Local\\Programs\\Python\\Python312\\python.exe",
      "args": ["C:\\Users\\fersi\\Desktop\\RealSave\\mcp_server\\server.py"],
      "env": { "REALSAVES_OWNER": "fersi" }
    }
  }
}
```

(If the file already has other keys, add `mcpServers` next to them; don't replace the file.)

- **Absolute paths on purpose:** Claude Desktop doesn't start in this repo folder, and the
  full Python path avoids the Microsoft Store `python` stub (same reason as
  `worker/run-worker.ps1`).
- **`env`:** extra environment variables Claude Desktop gives the server process. That's
  how the server knows whose reels to serve.
- After **any** change (config *or* code), **fully quit** Claude Desktop (tray icon → Quit)
  and reopen: it starts the server once, at launch.

## Logs

Anything the server writes to **stderr** is captured by Claude Desktop. To read it:
**Settings → Developer → realsaves → Show logs**. (In the Microsoft Store build, no
`mcp-server-realsaves.log` file shows up in the `logs\` folder, so use the button.)

While developing, the **Inspector's Console tab** shows the same stderr lines live.

> ⚠️ **Never `print()` to stdout in this server.** With stdio, stdout *is* the protocol
> channel; a stray print corrupts it and Claude shows a connection error. Use the `log()`
> helper in `server.py` (it writes to stderr).
