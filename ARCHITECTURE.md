# Reel Saves — End-to-End Architecture

A living reference for the whole system: capture → store → resolve+transcribe → surface in Claude.
Phases 1 (ingest), 2 (worker) and 3 (MCP server + vector search) are built.

---

## 1. The vision

You consume a lot of short-form video (Instagram / TikTok / Facebook). Some of it is genuinely
useful — hooks, ideas, techniques. Today that value is trapped in a video you'll never scroll back to.

Reel Saves turns it into an **AI-queryable knowledge base**: one-tap capture → background
transcription → and when you open Claude to work on something, it searches your whole library by
meaning and asks *"I found 3 reels related to SEO, want to use them?"* Reels are a **library, not
an inbox**: one reel can help in many chats.

The end payoff: **the reel becomes text your AI can reason over while you work.**

---

## 2. The big picture

```mermaid
flowchart TD
    A["📱 iOS Shortcut<br/>(share sheet)"] -->|POST link + note| B["Ingest API<br/>/api/saves<br/>(Vercel)"]
    B -->|store raw, fast, no scraping| C[("MongoDB Atlas<br/>saves collection")]

    C -->|"claims status:pending"| D["Worker<br/>(LOCAL / home IP)"]
    D -->|"yt-dlp: resolve + download audio"| E["Platforms<br/>IG / TikTok / FB"]
    D -->|"Whisper: audio → text"| D
    D -->|"write transcript + real ID, status:ready"| C

    C -->|"reads"| F["MCP Server<br/>(LOCAL, reads Atlas)"]
    F <-->|"tools: find_reels / get_reel / list_new_reels"| G["🧠 Claude<br/>(Desktop)"]

    style B fill:#2d6a4f,color:#fff
    style D fill:#5a189a,color:#fff
    style F fill:#1d3557,color:#fff
    style C fill:#003049,color:#fff
```

**Golden rule that answers the "will we get banned?" question:** only the **Worker** ever contacts
the platforms, and it runs **locally on your home (residential) IP** — which the platforms block
far less than datacenter IPs. The API and MCP server never touch IG/TikTok/FB at all.

---

## 3. Components

| Component | Where it runs | Talks to platforms? | Ban risk | Status |
|---|---|---|---|---|
| **iOS Shortcut** | Your phone | No | none | ✅ working |
| **Ingest API** (`/api/saves`) | Vercel (serverless) | No — only stores | **none** | ✅ built |
| **MongoDB Atlas** | Cloud (M0 free) | No | none | ✅ in use (+ vector index) |
| **Worker** (resolve + transcribe + embed) | **Local machine** | Yes — yt-dlp + audio download | low (home IP, low volume) | ✅ built |
| **MCP Server** | **Local** (Claude connects) | No — reads Atlas | none | ✅ built |

Atlas is the **shared hand-off**: the API writes rows; the worker and MCP server read/update them.
This decoupling is what lets the API live in the cloud while the risky worker stays home.

---

## 4. Data lifecycle (the status state machine)

```mermaid
stateDiagram-v2
    [*] --> pending: API stores a parsed save
    [*] --> unparsed: link shape unknown (kept, not processed)
    pending --> processing: worker claims it (atomic)
    processing --> ready: yt-dlp + Whisper succeed → transcript stored
    processing --> failed: download blocked / FB login wall / no audio
    failed --> pending: manual/auto retry (limited attempts)
    ready --> [*]
```

- `status` is the transcription hook. `resolved` (boolean) is the *separate* hook meaning
  "the real ID/URL is known" — Instagram is `resolved:true` at ingest; TikTok/Facebook become
  `resolved:true` once yt-dlp follows the redirect.
- `read` (boolean) feeds `list_new_reels`. Since the "library, not inbox" reframe, search
  covers **all** `ready` reels, read or not.

---

## 5. Schema evolution

**Phase 1 (built, multi-tenant) — already stores everything the pipeline needs to start:**

```
owner, platform, contentId, dedupKey, resolved, url, rawUrl,
note, source, capturedAt, status, read, transcript
```

Every save carries an **`owner`** (from the user's API key). The unique index is compound
`{owner, dedupKey}` and *partial* (only where `dedupKey` is a string), so each user dedups
within their own reels and unparsed rows never collide. See `README.md` for the multi-tenant
auth model (`API_KEYS` = `owner:key` pairs).

**Phase 2 adds (filled by the worker, mostly free from yt-dlp's metadata):**

```
title            // yt-dlp's title (a placeholder on Instagram: "Video by <handle>")
caption          // the post's real caption (yt-dlp `description`), used for display/relevance
uploader         // @handle / page
durationSec      // length (Instagram reports none → measured from the audio)
thumbnailUrl     // preview image
transcript       // ← the whole point: the text
transcriptLang   // detected language (e.g. "en", "ar")
segments         // optional: [{start, end, text}] for timestamped quotes
error            // failure reason when status:"failed"
attempts         // retry counter
processedAt      // when transcription finished
```

**Phase 3 adds (filled by the worker / `embed_backfill.py`):**

```
embedding        // 1024 numbers: caption + note + transcript, embedded with bge-m3
embeddingModel   // e.g. "BAAI/bge-m3@5617a9f6" — vectors are only comparable within one model
embeddedAt       // when the vector was computed
```

No re-architecture needed — every later field is additive.

---

## 6. The Worker in detail (phase 2)

The worker is a small loop that runs on your machine. Per cycle:

```mermaid
flowchart LR
    P["1. Claim<br/>findOneAndUpdate<br/>pending → processing"] --> Y["2. yt-dlp -J url<br/>(resolve real ID +<br/>metadata)"]
    Y --> DK["3. Reconcile dedup<br/>real ID vs existing rows"]
    DK --> AU["4. yt-dlp -x<br/>(download audio only)"]
    AU --> W["5. Whisper<br/>(audio → text)"]
    W --> S["6. Save transcript,<br/>status → ready,<br/>delete temp audio"]
    Y -.login wall / blocked.-> F["status → failed<br/>+ error"]
    AU -.-> F
    W -.-> F
```

**Why yt-dlp does steps 2 & 4:** given a short link (`vt.tiktok.com/…`, `facebook.com/share/r/…`),
`yt-dlp --dump-json <url>` follows the redirect and returns JSON with the **real numeric id**,
canonical `webpage_url`, `title`, `uploader`, `duration`, `thumbnail`. That *is* our resolver — no
manual HTTP redirect handling. Then `yt-dlp -x --audio-format mp3` pulls **audio only** (lighter than
video, and all Whisper needs). yt-dlp supports Instagram, TikTok, and Facebook and is community-
maintained against site changes.

**Reconcile (step 3):** once the real TikTok/FB id is known, update `contentId`/`dedupKey` to
`platform:<realid>` and `resolved:true`. If another row already has that real id (same video shared
twice via different share codes), merge — keep one, drop the duplicate.

**Language: Node or Python?** The rest of the stack is Node. Two clean options:
- **Node worker that shells out** to the `yt-dlp` binary and a Whisper CLI (`whisper.cpp` or
  `faster-whisper`). One codebase language, binaries do the heavy lifting. ← recommended.
- **Python worker** — natural fit since yt-dlp and faster-whisper are Python libraries.

---

## 7. Transcription deep dive — Whisper

**What it is:** [Whisper](https://github.com/openai/whisper) is OpenAI's open-source speech-to-text
model — **MIT-licensed, free, runs entirely on your machine, no API cost, no per-minute billing.**
It handles 90+ languages and is robust to accents, background music, and noise (exactly the reel case).

### Which Whisper flavor to run locally

| Flavor | What it is | Best when |
|---|---|---|
| `openai-whisper` | Original Python/PyTorch reference | GPU available, want the canonical impl |
| **`faster-whisper`** | CTranslate2 reimpl — ~4× faster, less RAM, int8 CPU mode | **Recommended default** (fast on CPU) |
| `whisper.cpp` | C/C++ port, single binary, no Python | Windows/Mac with no GPU, want zero deps |

For this project (Windows, short clips, no guaranteed GPU): **`faster-whisper` with `int8` on CPU**,
or **`whisper.cpp`** if you'd rather call a standalone binary from the Node worker.

### Model sizes (accuracy vs speed vs disk)

| Model | Params | Download | Rel. speed | Accuracy | Notes |
|---|---|---|---|---|---|
| tiny | 39M | ~75 MB | ~10× | low | quick smoke tests |
| base | 74M | ~145 MB | ~7× | ok | fast rough drafts |
| **small** | 244M | ~460 MB | ~4× | good | **recommended starting point** |
| medium | 769M | ~1.5 GB | ~2× | better | bump to this if transcripts are rough |
| large-v3 | 1.5B | ~3 GB | 1× | best | hardest audio / heavy accents |

(Speeds are relative to `large`; approximate.) Reels are short (15–90 s), so even `medium` finishes
in seconds-to-tens-of-seconds on CPU. Start at **`small`**, move to `medium` only if quality demands.

### CPU vs GPU
GPU (CUDA) is much faster, but because reels are short and this runs in the background, **CPU is
perfectly fine.** No GPU purchase needed to ship this.

### Language handling (important for you)
Whisper **auto-detects** the language and transcribes in the original — so Arabic/French/English
reels all work. It can also **translate to English** (`task=translate`) if you'd prefer every
transcript in English for uniform searching. We can store `transcriptLang` and decide per-reel.

### Input / output
- **Input:** audio only (mp3/wav/m4a from yt-dlp). Whisper internally resamples to 16 kHz mono.
- **Output:** plain text, and optionally **timestamped segments** — useful later for "at 0:42 they
  say…" citations inside Claude.

### The free-vs-hosted tradeoff
- **Local Whisper (chosen):** free forever, private (audio never leaves your machine), one-time model
  download, uses your CPU/GPU.
- **Hosted fallback (optional, not now):** OpenAI Whisper API (~$0.006/min) or Groq's very cheap/fast
  Whisper — zero local compute, tiny cost, but sends audio to a third party. Keep in back pocket only
  if local proves too slow.

---

## 8. Avoiding bans / blocks (the concern, addressed)

- **All platform contact lives in the local worker** on your **residential IP** — the single biggest
  factor. Datacenter IPs (Vercel/AWS) get fingerprinted and rate-limited aggressively; home IPs read
  as normal users. This is *why* the worker never goes on Vercel.
- **Low volume + politeness:** process sequentially (not in parallel), add a small random delay
  between jobs, keep yt-dlp updated.
- **Audio-only** downloads are lighter and less scraper-like than full video.
- **Facebook login/checkpoint wall** → treat as `status:"failed"` with the reason; don't retry-hammer.
  (For private/age-gated content, yt-dlp can use exported browser cookies — add only if needed.)
- The **ingest API carries zero risk** — it never contacts the platforms — so deploying it publicly is
  completely safe.

---

## 9. The MCP layer & vector search (phase 3)

The MCP server exposes **read-only tools** Claude can call
([`mcp_server/README.md`](mcp_server/README.md)):

- `find_reels(topic)` — reels related to a topic, **by meaning and in any language** (vector search)
- `get_reel(id)` — one reel in full, read by Claude to apply its ideas
- `list_new_reels` — the newest unread saves

**The flow (topic-triggered RAG):** your first message states a task (*"today: SEO for our landing
page"*) → Claude calls `find_reels` with a few focused queries → tells you which reels fit and asks
→ on *yes*, reads them with `get_reel` and applies their ideas to the task (no transcript recap) →
on *no*, carries on without them. That is **R**etrieval → **A**ugmented → **G**eneration.

**The honest caveat:** MCP is **pull-based** — the server can't push into your chat. The trigger is
a standing instruction in Claude Desktop's personal instructions (wording in the MCP README).

**Relevance = vector search** (decided over keywords and over "send Claude every summary": keywords
miss synonyms and cross-language matches; sending every summary costs tokens in every chat and grows
with the library). Details and the measurements: [`VECTOR-SEARCH-EXPLAINED.md`](VECTOR-SEARCH-EXPLAINED.md)
and [`shared/README.md`](shared/README.md).

- **Model:** `bge-m3`, local (onnxruntime, no PyTorch), multilingual. Chosen by measuring it against
  `multilingual-e5-small` on 76 real reels: 84% vs 75% of expected reels in the top 5.
- **Write path:** the worker embeds each reel once, after transcription.
- **Read path:** the MCP server embeds the topic with the **same** model, then Atlas `$vectorSearch`
  (index `reels_vector`, cosine, pre-filtered on owner / `status: "ready"` / `embeddingModel`).
- **Two relevance filters:** a measured similarity threshold in the server (0.50), then Claude's
  own judgment — vector search always returns *something*, even when nothing is related.

## 10. Deployment topology (what runs where)

```mermaid
flowchart TB
    subgraph Phone
      SC["iOS Shortcut"]
    end
    subgraph Cloud
      V["Vercel: Ingest API"]
      AT[("MongoDB Atlas")]
    end
    subgraph "Your machine (home IP)"
      WK["Worker: yt-dlp + Whisper"]
      MCP["MCP Server"]
      CL["Claude Desktop"]
    end
    SC --> V --> AT
    WK <--> AT
    MCP <--> AT
    CL <--> MCP
```

- **Cloud:** ingest API + Atlas (public, always-on → phone captures anytime, no PC needed, no tunnel).
- **Home:** worker (residential IP, low block risk) + MCP server (Claude Desktop connects locally,
  reads Atlas).

---

## 11. Roadmap

- **Phase 1 — Ingest (DONE):** API, parsing, dedup, storage, logging. Tested locally end-to-end.
- **Phase 1.5 — Deploy (DONE):** Vercel + Atlas.
- **Phase 2 — Worker (DONE):** `yt-dlp` (resolve + audio) → `faster-whisper` (transcript) → `ready`;
  failures/retries; TikTok/FB duplicate reconciliation; Task Scheduler runs.
- **Phase 3 — MCP + vector search (DONE):** stdio MCP server (`find_reels`, `get_reel`,
  `list_new_reels`), local bge-m3 embeddings, Atlas Vector Search, topic-triggered RAG via Claude
  Desktop instructions. (`mark_read` dropped by the "library, not inbox" reframe.)
- **Next — efficiency:** "key ideas" per reel written once at save time so `get_reel` can skip the
  full transcript; shorter search summaries. Optional: RealSaves in Claude Code, a remote (HTTP) MCP
  server for claude.ai web/mobile.
```
