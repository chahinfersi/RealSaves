# RealSaves Worker (Phase 2)

The local worker that finishes each saved reel: **resolve → download audio → transcribe →
`ready`**. It runs on your machine (home IP) and talks to the same MongoDB the ingest API
writes to. See [`../ARCHITECTURE.md`](../ARCHITECTURE.md) §6–8 for the why.

## What it does

Each run first **reclaims stale rows** — any reel left stuck in `processing` by a previous
crash (dead battery, kill) for more than 15 min is reset to `pending` (or marked `failed`
once it has burned through `MAX_ATTEMPTS`), so nothing is orphaned. Then, for every row with
`status: "pending"`:

1. **Claims** it atomically (`pending → processing`) so two runs never grab the same reel.
2. **Resolves** it with `yt-dlp` — follows the short-link redirect to the real numeric id +
   `title`, `uploader`, `duration`, `thumbnail`.
3. **Reconciles dedup** — TikTok/Facebook short codes become `platform:<realid>`, `resolved:true`.
   If the same video already exists for that owner (shared via two codes), it drops the
   duplicate and keeps the note.
4. **Downloads audio-only** (`yt-dlp` + ffmpeg → mp3).
5. **Transcribes** with local Whisper (`faster-whisper`, `small`, CPU/int8).
6. Writes `transcript`, `transcriptLang`, `segments` + metadata and flips to `ready`; deletes
   the temp audio.

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
