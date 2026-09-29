# Wrapper that the Windows Task Scheduler runs on a timer.
#
# Task Scheduler launches a program, not a shell, so it can't redirect output to a file
# by itself. This tiny wrapper does three things:
#   1. finds the real Python (not the Microsoft Store stub),
#   2. runs the worker once (which self-heals stale rows, then processes any pending),
#   3. appends everything it printed, with a timestamp, to worker/worker.log.
#
# The worker reads the project-root .env for MONGODB_URI / DB_NAME, so it hits whichever
# database that points at (local or Atlas) — no config needed here.

$ErrorActionPreference = "Continue"
$here  = $PSScriptRoot
$py    = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
$log   = Join-Path $here "worker.log"
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = "1"  # keep the log clean

# Capture stdout+stderr, then write as UTF-8 (PowerShell's >> redirect defaults to UTF-16,
# which mangles the worker's UTF-8 status symbols). Out-String keeps it one clean block.
Add-Content -Path $log -Value "`n===== run at $stamp =====" -Encoding utf8
& $py (Join-Path $here "worker.py") 2>&1 | Out-String | Add-Content -Path $log -Encoding utf8
