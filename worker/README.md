# RealSaves Worker (Phase 2)

The local worker that finishes each saved reel: **resolve → download audio → transcribe →
embed → `ready`**. It runs on your machine (home IP) and talks to the same MongoDB the ingest API
writes to. See [`../ARCHITECTURE.md`](../ARCHITECTURE.md) §6–8 for the why.

## What it does

Each run first **reclaims stale rows** — any reel left stuck in `processing` by a previous
crash (dead battery, kill) for more than 15 min is reset to `pending` (or marked `failed`
once it has burned through `MAX_ATTEMPTS`), so nothing is orphaned. Then, for every row with
`status: "pending"`:

1. **Claims** it atomically (`pending → processing`) so two runs never grab the same reel.
2. **Resolves** it with `yt-dlp` — follows the short-link redirect to the real numeric id +
   `title`, `caption` (the post's real caption, from yt-dlp's `description`), `uploader`,
   `duration`, `thumbnail`.
3. **Reconciles dedup** — TikTok/Facebook short codes become `platform:<realid>`, `resolved:true`.
   If the same video already exists for that owner (shared via two codes), it drops the
   duplicate and keeps the note.
4. **Downloads audio-only** (`yt-dlp` + ffmpeg → mp3).
5. **Transcribes** with local Whisper (`faster-whisper`, `small`, CPU/int8).
6. **Embeds** caption + note + transcript for vector search (Phase 3): a local bge-m3 model
   turns the text into `embedding` (1024 numbers) + `embeddingModel`, see
   [`../shared/README.md`](../shared/README.md). If this step fails, the reel still becomes
   `ready` (the transcript matters most) and `embed_backfill.py` adds the vector later.
7. Writes `transcript`, `transcriptLang`, `segments` + metadata and flips to `ready`; deletes
   the temp audio. If the platform reported no duration (Instagram never does), `durationSec`
   is taken from the audio length Whisper measured.

A login/permission wall (Facebook especially) or any error → `status: "failed"` with a reason
in `error`. It never stores a garbage id and never crashes the whole run over one bad row.

## Setup (one time)

Requires **Python 3.12+**, **ffmpeg** on `PATH`, and the Python deps:

```bash
pip install -r worker/requirements.txt
```

`MONGODB_URI` and `DB_NAME` are read from the project-root `.env` (same as the API).

## Run

From the project root:

```bash
python worker/worker.py                  # process everything pending, then exit
python worker/worker.py --limit 1        # just one row (handy while testing)
python worker/worker.py --model medium   # bump accuracy if `small` is too rough
python worker/worker.py --translate      # force English transcripts (default: original language)
python worker/worker.py --reclaim-minutes 30  # widen the "stuck" window (default 15)
```

First run downloads the Whisper `small` model (~460 MB) once, then it's cached. The worker
also ensures a **partial index** on `{status, capturedAt}` (only `pending` rows), so claiming
the next job stays instant as the collection grows.

> **ffmpeg not on PATH?** Set `FFMPEG_LOCATION` to its `bin` directory and the worker will
> pass it to yt-dlp.

## Backfill (rows transcribed before a new field existed)

[`backfill.py`](backfill.py) gives already-`ready` reels the fields newer worker versions
store — today `caption` and `durationSec` — **without re-transcribing**. It only adds those
two fields (never touches `status` or the transcript), so a reel that has since disappeared is
simply skipped. Rows already complete aren't selected, so re-running it is harmless.

```bash
python worker/backfill.py --dry-run   # list what would change, no network calls
python worker/backfill.py --limit 2   # try a couple first
python worker/backfill.py             # do them all
```

Captions come from metadata only (no download); Instagram durations need the audio, so those
rows download it, measure its length, and delete it.

## Embedding backfill (vector search)

[`embed_backfill.py`](embed_backfill.py) gives `ready` reels their vector: every `ready` row
whose `embeddingModel` isn't the current model, which covers rows from before Phase 3 *and*
a later model change. It only sets `embedding`, `embeddingModel`, `embeddedAt`, runs fully
locally (no platform contact), and is safe to re-run.

```bash
python worker/embed_backfill.py --dry-run   # count, write nothing
python worker/embed_backfill.py --limit 4   # try a few first
python worker/embed_backfill.py             # do them all (~2 s per reel on CPU)
```

The first run downloads the model once (~2.3 GB, cached by Hugging Face).

## Automated runs (Windows Task Scheduler)

A scheduled task named **`RealSaves Worker`** runs [`run-worker.ps1`](run-worker.ps1) every
2 hours. That wrapper runs the worker once and appends its output to **`worker/worker.log`**.
Key settings: *start when available* (catches up after the PC was off), *run only while
you're logged in*, and *don't start a second copy if one is running*.

```powershell
# See it / its next run                (PowerShell)
Get-ScheduledTaskInfo -TaskName "RealSaves Worker"
# Run it right now (don't wait for the timer)
Start-ScheduledTask   -TaskName "RealSaves Worker"
# Pause / resume
Disable-ScheduledTask -TaskName "RealSaves Worker"
Enable-ScheduledTask  -TaskName "RealSaves Worker"
# Remove it entirely
Unregister-ScheduledTask -TaskName "RealSaves Worker" -Confirm:$false
```

Change the interval in Task Scheduler (taskschd.msc) → *RealSaves Worker* → Triggers, or
re-register with a different `-RepetitionInterval`. Because it's a **local** task, it only
runs while the PC is on; unprocessed reels simply wait as `pending` until it next runs. For
true 24/7, move the same worker to an always-on home device (e.g. a Raspberry Pi) — never a
cloud VM (datacenter IPs get blocked; the home IP is the whole point).

## Notes / follow-ups (not built yet)

- **`--loop` mode** — a continuous poll instead of run-once (Task Scheduler covers this for now).
- **Move to an always-on home device** — a Raspberry Pi keeps the residential IP and removes
  the "PC must be on" limit.
- **Better Arabic-dialect transcripts** — `small` mishears tech words in Tunisian/Egyptian
  reels ("NLMS" for LLMs). Deferred: Claude understands them from context; revisit (e.g.
  `large-v3-turbo` or an `initial_prompt` with tech vocabulary) if search quality on Arabic
  reels turns out to suffer. (So far vector search copes: it matches on meaning, not exact
  words.)
- **"Key ideas" per reel** — have the worker write 3–5 bullet points once, so `get_reel` can
  return those instead of the full transcript and save tokens in every chat.
