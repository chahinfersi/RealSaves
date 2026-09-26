# RealSaves — Ingest API

A small, **multi-tenant** serverless API that captures short videos shared from an iOS
Shortcut — across **Instagram, TikTok and Facebook** — and stores them in MongoDB, so a
later MCP server can pull them into a Claude session.

The one Shortcut sits in the share sheet of every app, so the endpoint accepts whatever
link the platform hands over and tags each save with its `platform` and its `owner`.

> **Status:** the ingest layer is built and **deployed** (Vercel + MongoDB Atlas), tested
> end-to-end from the phone. Transcription isn't built yet — the schema is shaped so a
> worker can slot in later. The `status` field is the hook (`pending → processing → ready
> → failed`), and `resolved` tells the worker when it still needs to follow a redirect.
>
> See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full system design and
> [`LEARNING-LOG.md`](LEARNING-LOG.md) for the roadmap + concepts.

## Multi-tenancy — one API, many users

Each user has their own API key. The key both **authenticates** (is it valid?) and
**identifies** (whose reels are these?). Every save is stamped with an `owner`, and each
user only ever reads back their own reels — same URL, same database, isolated data.

Keys are configured in one env var, `API_KEYS`, as comma-separated `owner:key` pairs:

```
API_KEYS="fersi:6f1a…,sami:9c22…"
```

The `x-api-key` header sends **only the key** (the hex) — never the `owner:` prefix.

## How each platform's link is handled

| Platform | Example share-sheet link | ID in the URL? |
|---|---|---|
| **Instagram** | `instagram.com/reel/<id>/?stkn=…` | ✅ Yes — real ID extracted immediately, URL cleaned. |
| **TikTok** | `vt.tiktok.com/<code>/` | ❌ No — short link; real video ID is behind a redirect. |
| **Facebook** | `facebook.com/share/r/<token>/` | ❌ No — share link; real reel ID is behind a redirect. |

For TikTok/Facebook the POST does **no network call** (it must answer in under a second).
It stores the save with a **provisional ID** (the short code), `resolved: false`, and
`status: "pending"`. A later worker follows the redirect to fill in the real canonical ID
and URL, then flips `resolved: true`. Unknown platforms (e.g. YouTube) are still **kept**
with `status: "unparsed"` rather than rejected.

## Endpoints

Both live at `/api/saves` and require an `x-api-key` header matching one of the keys in
`API_KEYS`.

### `POST /api/saves`
Body: `{ "link": string, "note"?: string }`

- Detects the platform and extracts the best available ID, storing a cleaned URL (tracking
  params like `?stkn=`, `?igsh=`, `?mibextid=`, `utm_*` are stripped; `rawUrl` keeps the original).
- Unrecognized links are still **kept** with `dedupKey: null`, `status: "unparsed"`.
- Deduplicates per owner on `dedupKey` (`platform:id`). Re-saving doesn't create a
  duplicate; if a note is sent and *your* existing record has none, the note is filled in
  (an existing note is never overwritten, and another user's rows are never touched).
- Short, notification-friendly responses (include the detected platform):
  - `{"ok":true,"msg":"saved","platform":"tiktok"}`
  - `{"ok":true,"msg":"already saved","duplicate":true,"platform":"instagram"}`

### `GET /api/saves`
Query params:
- `limit` — default `20`, clamped to `100`.
- `unreadOnly` — `true`/`1` returns only unread saves.

Returns *your* saves, newest first: `{"ok":true,"count":n,"saves":[...]}`.

## Data model — `saves` collection

| Field | Type | Notes |
|---|---|---|
| `owner` | string | which user this reel belongs to (from their API key) |
| `platform` | string | `"instagram"` \| `"tiktok"` \| `"facebook"` \| `"unknown"` |
| `contentId` | string \| null | canonical ID (Instagram) or provisional short code (TikTok/Facebook); `null` when unparsed |
| `dedupKey` | string \| null | `"<platform>:<contentId>"`; part of the unique key. `null` for unparsed saves |
| `resolved` | boolean | `true` when `contentId`/`url` are canonical; `false` = worker still needs to follow the redirect |
| `url` | string | cleaned URL; provisional (== `rawUrl`) for unresolved short links |
| `rawUrl` | string | exactly as received |
| `note` | string \| null | optional dictated note |
| `source` | string | `"ios-shortcut"` |
| `capturedAt` | Date | server time at ingest |
| `status` | string | `"pending"` normally; `"unparsed"` when the link couldn't be parsed |
| `read` | boolean | `false` on create |
| `transcript` | null | placeholder for the worker |

A **unique, partial** index on `{ owner, dedupKey }` enforces per-user dedup — two users
can each save the same reel. It's *partial* (only where `dedupKey` is a string) so any
number of unparsed rows (null `dedupKey`) are allowed per owner. Created automatically on
first DB connection.

**Two "to-do" flags for the worker:** `resolved: false` means "follow the redirect to get
the real ID/URL" (TikTok/Facebook); `status: "pending"` means "still needs transcription."
Instagram saves come in already `resolved: true`.

## Project structure

```
api/saves.js        # single handler, branches on req.method (GET | POST)
lib/db.js           # cached MongoClient across warm invocations + ensures index
lib/auth.js         # x-api-key -> owner (multi-tenant)
lib/parseLink.js    # platform detection + ID extraction + URL cleaning (has a self-test)
dev-server.js       # local-only HTTP server (not used by Vercel) — `npm run local`
.env.example        # MONGODB_URI, DB_NAME, API_KEYS
vercel.json         # function config
ARCHITECTURE.md     # full system design
LEARNING-LOG.md     # roadmap + concepts learned
```

## Environment variables

| Name | Example | Notes |
|---|---|---|
| `MONGODB_URI` | `mongodb+srv://user:pass@cluster0.xxxx.mongodb.net/...` | Atlas connection string (or `mongodb://127.0.0.1:27017` locally) |
| `DB_NAME` | `reelsaves` | database name (created lazily on first write) |
| `API_KEYS` | `fersi:6f1a…,sami:9c22…` | comma-separated `owner:key` pairs |

Generate a strong key:
```bash
node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"
```

## Run locally

```bash
npm install
cp .env.example .env      # then edit: MONGODB_URI, DB_NAME, API_KEYS
npm run local             # http://localhost:3000/api/saves against your .env
```
Sanity-check the parser without any DB:
```bash
npm run test:parse
```

## Deploy (GitHub → Vercel)

1. Push the repo to GitHub.
2. Vercel → **Add New… → Project** → import the repo. **Framework Preset: Other**, no build step.
3. Add the three env vars (`MONGODB_URI`, `DB_NAME`, `API_KEYS`) for **Production**.
4. In Atlas → **Network Access**, allow `0.0.0.0/0` (Vercel IPs rotate; the DB password is the real lock).
5. **Deploy.** Every `git push` auto-deploys after that.

**Three gotchas worth remembering:**
- **Env vars are baked at deploy time** — change one → **redeploy** for it to take effect.
- **Use the production URL** (`https://real-saves.vercel.app/api/saves`), not the immutable
  `…-<hash>-….vercel.app` deployment URL (which is frozen to an old build).
- **Deployment Protection** (Settings → Deployment Protection → Vercel Authentication) must
  be **off** for a public API — your `x-api-key` is the real auth.

## Onboard another user (multi-tenant)

1. Generate a fresh key: `node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"`.
2. **Edit** `API_KEYS` in Vercel — append the new pair: `fersi:xxx,sami:<newkey>`.
3. **Redeploy.**
4. Give them: the production URL, their key (**just the hex, no `owner:` prefix**), and the Shortcut.

Their saves are stamped `owner: "sami"` and stay isolated from everyone else. To revoke a
user, remove their `owner:key` pair and redeploy.

## Test it

PowerShell (Windows):
```powershell
$base = "https://real-saves.vercel.app/api/saves"
$hdr  = @{ "x-api-key" = "<your hex key>" }

# save
Invoke-RestMethod -Method Post -Uri $base -Headers $hdr -ContentType "application/json" `
  -Body '{"link":"https://www.instagram.com/reel/DdhRKxUjxnd/?stkn=abc","note":"great hook"}'

# list your reels, unread only
Invoke-RestMethod -Uri "$base`?limit=10&unreadOnly=true" -Headers $hdr
```

curl (macOS/Linux/Git Bash):
```bash
KEY="<your hex key>"; BASE="https://real-saves.vercel.app"
curl -X POST "$BASE/api/saves" -H "x-api-key: $KEY" -H "content-type: application/json" \
  -d '{"link":"https://vt.tiktok.com/ZSbRaJKpf/"}'
# {"ok":true,"msg":"saved","platform":"tiktok"}

curl "$BASE/api/saves?limit=10" -H "x-api-key: $KEY"
```

Auth failure (missing/wrong key) → `401 {"ok":false,"msg":"unauthorized"}`.

## iOS Shortcut (capture)

1. **Receive** from the share sheet (URLs).
2. **Get URLs from Input** — extracts a clean URL (fixes the "RTF → URL" error).
3. **Get Contents of URL:**
   - URL: `https://real-saves.vercel.app/api/saves`
   - Method: `POST`
   - Header: `x-api-key` = your key
   - Request Body: **JSON** → `link` = the URL from step 2
4. **Show Notification** with *Contents of URL* to see `saved` / `already saved`.

## Notes
- The POST handler does no blocking work and responds well under a second, so the
  Shortcut's short background timeout is respected.
- The Mongo connection is cached on a global and reused across warm invocations (the
  standard serverless pattern; avoids exhausting the Atlas connection pool).
