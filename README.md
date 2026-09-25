# Reel Saves — Ingest API (Phase 1)

A tiny serverless API that captures short videos shared from an iOS Shortcut — across
**Instagram, TikTok and Facebook** — and stores them in MongoDB, so a later MCP server
can pull them into a Claude session.

The one Shortcut sits in the share sheet of every app, so the endpoint accepts whatever
link the platform hands over and tags each save with its `platform`.

**Phase 1 is ingest + storage only.** No transcription yet — but the schema is shaped
so a worker can slot in later. The `status` field is the hook
(`pending → processing → ready → failed`).

## How each platform's link is handled

The link the share sheet gives you differs by platform:

| Platform | Example share-sheet link | ID in the URL? |
|---|---|---|
| **Instagram** | `instagram.com/reel/<id>/?stkn=…` | ✅ Yes — real ID extracted immediately, URL cleaned. |
| **TikTok** | `vt.tiktok.com/<code>/` | ❌ No — it's a short link; real video ID is behind a redirect. |
| **Facebook** | `facebook.com/share/r/<token>/` | ❌ No — it's a share link; real reel ID is behind a redirect. |

For TikTok/Facebook the POST does **no network call** (it must answer in under a second).
It stores the save with a **provisional ID** (the short code), `resolved: false`, and
`status: "pending"`. A later worker follows the redirect to fill in the real canonical
ID and URL, then flips `resolved: true`. Unknown platforms (e.g. YouTube) are still
**kept** with `status: "unparsed"` rather than rejected.

## Endpoints

Both live at `/api/saves` and require a `x-api-key` header matching `API_KEY`.

### `POST /api/saves`
Body: `{ "link": string, "note"?: string }`

- Detects the platform and extracts the best available ID, storing a cleaned URL (query
  params like `?stkn=`, `?igsh=`, `?mibextid=`, `utm_*` are stripped).
- Unrecognized links are still **kept** with `dedupKey: null`, `status: "unparsed"`.
- Deduplicates on `dedupKey` (`platform:id`). Re-saving does not create a duplicate; if a
  note is sent and the existing record has none, the note is filled in (an existing note
  is never overwritten).
- Short, notification-friendly responses (include the detected platform):
  - `{"ok":true,"msg":"saved","platform":"tiktok"}`
  - `{"ok":true,"msg":"already saved","duplicate":true,"platform":"instagram"}`

### `GET /api/saves`
Query params:
- `limit` — default `20`, clamped to `100`.
- `unreadOnly` — `true`/`1` returns only unread saves.

Returns newest first: `{"ok":true,"count":n,"saves":[...]}`.

## Data model — `saves` collection

| Field | Type | Notes |
|---|---|---|
| `platform` | string | `"instagram"` \| `"tiktok"` \| `"facebook"` \| `"unknown"` |
| `contentId` | string \| null | canonical ID (Instagram) or provisional short code (TikTok/Facebook); `null` when unparsed |
| `dedupKey` | string \| null | `"<platform>:<contentId>"`; the unique key. `null` for unparsed saves |
| `resolved` | boolean | `true` when `contentId`/`url` are canonical; `false` = worker still needs to follow the redirect |
| `url` | string | cleaned URL; provisional (== `rawUrl`) for unresolved short links |
| `rawUrl` | string | exactly as received |
| `note` | string \| null | optional dictated note |
| `source` | string | `"ios-shortcut"` |
| `capturedAt` | Date | server time at ingest |
| `status` | string | `"pending"` normally; `"unparsed"` when the link couldn't be parsed |
| `read` | boolean | `false` on create |
| `transcript` | null | placeholder for the week-2 worker |

A **unique, sparse** index on `dedupKey` enforces dedup (sparse so many `null` unparsed
docs don't collide). It's created automatically on first DB connection.

**Two "to-do" flags for the worker:** `resolved: false` means "follow the redirect to get
the real ID/URL" (TikTok/Facebook), and `status: "pending"` means "still needs
transcription." Instagram saves come in already `resolved: true`.

## Project structure

```
api/saves.js        # single handler, branches on req.method (GET | POST)
lib/db.js           # cached MongoClient across warm invocations + ensures index
lib/auth.js         # x-api-key check
lib/parseLink.js    # platform detection + ID extraction + URL cleaning (has a self-test)
.env.example        # MONGODB_URI, DB_NAME, API_KEY
vercel.json         # function config
```

## Setup

### 1. Prerequisites
- Node 20
- A [Vercel](https://vercel.com) account + the Vercel CLI (`npm i -g vercel`)
- A [MongoDB Atlas](https://www.mongodb.com/atlas) account

### 2. Create a MongoDB Atlas M0 cluster and get the connection string
1. Atlas → **Build a Database** → **M0** (free) → create.
2. **Database Access** → **Add New Database User** (username + password; save the password).
3. **Network Access** → **Add IP Address** → **Allow Access from Anywhere** (`0.0.0.0/0`).
   Vercel's serverless IPs aren't fixed, so this is required for Hobby.
4. **Connect** → **Drivers** → copy the `mongodb+srv://…` string. Replace `<password>`
   with the DB user's password. That value is your `MONGODB_URI`.

### 3. Install and configure locally
```bash
npm install
cp .env.example .env
# edit .env: set MONGODB_URI, DB_NAME, API_KEY
```
Generate a strong API key:
```bash
openssl rand -hex 32
```

### 4. Run locally
```bash
npm run dev          # vercel dev — serves http://localhost:3000/api/saves and loads .env
```
Sanity-check the parser without any DB:
```bash
npm run test:parse
```

### 5. Set env vars in Vercel and deploy
```bash
vercel                                   # link the project (first time)
vercel env add MONGODB_URI               # paste value; choose Production + Preview
vercel env add DB_NAME
vercel env add API_KEY
vercel --prod                            # deploy
```
(You can also add these under **Project → Settings → Environment Variables** in the
Vercel dashboard.)

## Test with curl

Set your key and base URL first:
```bash
API_KEY="your-api-key"
BASE="https://<your-app>.vercel.app"   # or http://localhost:3000 with `npm run dev`
```

Save an Instagram reel (query params stripped automatically):
```bash
curl -X POST "$BASE/api/saves" \
  -H "x-api-key: $API_KEY" \
  -H "content-type: application/json" \
  -d '{"link":"https://www.instagram.com/reel/DdhRKxUjxnd/?stkn=MXRjencyamlocm95bw=="}'
# {"ok":true,"msg":"saved","platform":"instagram"}
```

Save a TikTok short link (stored provisionally, resolved later by the worker):
```bash
curl -X POST "$BASE/api/saves" \
  -H "x-api-key: $API_KEY" -H "content-type: application/json" \
  -d '{"link":"https://vt.tiktok.com/ZSbRaJKpf/"}'
# {"ok":true,"msg":"saved","platform":"tiktok"}
```

Save a Facebook share link:
```bash
curl -X POST "$BASE/api/saves" \
  -H "x-api-key: $API_KEY" -H "content-type: application/json" \
  -d '{"link":"https://www.facebook.com/share/r/1FfuwDrAqh/?mibextid=wwXIfr"}'
# {"ok":true,"msg":"saved","platform":"facebook"}
```

Save the same link again (dedup):
```bash
curl -X POST "$BASE/api/saves" \
  -H "x-api-key: $API_KEY" -H "content-type: application/json" \
  -d '{"link":"https://vt.tiktok.com/ZSbRaJKpf/"}'
# {"ok":true,"msg":"already saved","duplicate":true,"platform":"tiktok"}
```

Save with a note:
```bash
curl -X POST "$BASE/api/saves" \
  -H "x-api-key: $API_KEY" -H "content-type: application/json" \
  -d '{"link":"https://www.instagram.com/reel/OtherReel/","note":"great hook idea"}'
```

List the 10 most recent unread saves:
```bash
curl "$BASE/api/saves?limit=10&unreadOnly=true" -H "x-api-key: $API_KEY"
```

Auth failure (missing/wrong key):
```bash
curl -X POST "$BASE/api/saves" -H "content-type: application/json" -d '{"link":"x"}'
# 401 {"ok":false,"msg":"unauthorized"}
```

## Notes
- The POST handler does no blocking work and responds in well under a second, so the
  Shortcut's short background timeout is respected.
- The Mongo connection is cached on a global and reused across warm invocations rather
  than reopened per request (the standard serverless pattern; avoids exhausting the M0
  connection pool).
