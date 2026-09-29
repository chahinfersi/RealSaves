"""yt-dlp wrapper: resolve a link to its real metadata, and download audio-only.

Two jobs, both done by yt-dlp (see ARCHITECTURE.md section 6):
  1. extract_info(url)  -> follows the short-link redirect and returns the real numeric
                           id, canonical webpage_url, title, uploader, duration, thumbnail.
                           Downloads nothing. This is our "resolver".
  2. download_audio(url) -> pulls the audio stream only (lighter + all Whisper needs) and,
                           via ffmpeg, writes a single .mp3 we hand to the transcriber.

Facebook (and occasionally the others) can redirect to a login / checkpoint page instead
of the video. yt-dlp raises a DownloadError in that case; we wrap it in ResolveError so
the worker can mark the row `failed` with a clear reason instead of storing a garbage id.
"""

import os

import yt_dlp


class ResolveError(Exception):
    """A link we could not resolve/download (login wall, private, blocked, no audio)."""


# Substrings that usually mean "the platform showed a login/permission wall", not a real
# transient error. Kept for readability in the stored `error`; we fail either way.
_WALL_HINTS = (
    "login",
    "log in",
    "sign in",
    "logged in",
    "authenticate",
    "cookies",
    "private",
    "only available",
    "not available",
    "checkpoint",
    "rate-limit",
    "rate limit",
)


def _base_opts():
    opts = {
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "noprogress": True,
    }
    # In THIS session ffmpeg isn't on the shell PATH, so allow an explicit override.
    # In a normal terminal ffmpeg is on PATH and this env var is simply unset.
    ffmpeg_location = os.environ.get("FFMPEG_LOCATION")
    if ffmpeg_location:
        opts["ffmpeg_location"] = ffmpeg_location
    return opts


def extract_info(url):
    """Resolve `url` to a metadata dict WITHOUT downloading. Raises ResolveError on failure."""
    opts = _base_opts()
    opts["skip_download"] = True
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as err:
        raise ResolveError(_classify(err)) from err
    return _summarize(info)


def download_audio(url, out_dir):
    """Download audio-only for `url` into out_dir, returning the final .mp3 path.

    Raises ResolveError on failure. Caller is responsible for deleting the file.
    """
    os.makedirs(out_dir, exist_ok=True)
    opts = _base_opts()
    opts.update(
        {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(out_dir, "%(id)s.%(ext)s"),
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "5"}
            ],
        }
    )
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as err:
        raise ResolveError(_classify(err)) from err

    # After the FFmpegExtractAudio post-processor runs, the real output path is recorded
    # under requested_downloads[].filepath (extension is now .mp3).
    downloads = info.get("requested_downloads") or []
    if downloads and downloads[0].get("filepath"):
        return downloads[0]["filepath"]
    # Fallback: build the expected path from the id.
    guess = os.path.join(out_dir, f"{info.get('id')}.mp3")
    if os.path.exists(guess):
        return guess
    raise ResolveError("audio download produced no file")


def _summarize(info):
    """Pull just the fields we store from yt-dlp's large info dict."""
    duration = info.get("duration")
    return {
        "id": str(info.get("id")) if info.get("id") is not None else None,
        "platform": info.get("extractor_key", "").lower() or None,
        "webpageUrl": info.get("webpage_url"),
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("uploader_id"),
        "durationSec": int(duration) if isinstance(duration, (int, float)) else None,
        "thumbnailUrl": info.get("thumbnail"),
    }


def _classify(err):
    """Turn a yt-dlp DownloadError into a short, readable reason string."""
    msg = str(err).strip()
    low = msg.lower()
    for hint in _WALL_HINTS:
        if hint in low:
            return f"login/permission wall: {msg}"
    return msg
