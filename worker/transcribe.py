"""faster-whisper wrapper: turn an audio file into text.

faster-whisper is a CTranslate2 reimplementation of OpenAI Whisper — same models, much
faster and lighter on CPU. We run the `small` model in int8 on CPU (good accuracy, quick
on short reels). The model (~460 MB for `small`) is downloaded once on first use and then
cached under the Hugging Face cache dir.

By default we transcribe in the ORIGINAL language and let Whisper auto-detect it (so
Arabic / French / English reels all work). Pass translate=True to force English output.
"""

from faster_whisper import WhisperModel

_model = None
_model_size = None


def get_model(model_size="small"):
    """Load (once) and cache the Whisper model for the run."""
    global _model, _model_size
    if _model is None or _model_size != model_size:
        # int8 on CPU is the sweet spot for short clips with no GPU.
        _model = WhisperModel(model_size, device="cpu", compute_type="int8")
        _model_size = model_size
    return _model


def transcribe(audio_path, model_size="small", translate=False):
    """Transcribe `audio_path`. Returns {text, language, duration, segments:[{start,end,text}]}.

    `duration` is the audio length in seconds, measured by Whisper while decoding. The
    worker uses it when the platform didn't report one (Instagram never does).

    vad_filter trims silence/music-only stretches, which cuts the hallucinated text
    Whisper otherwise invents over non-speech audio (common in reels with background music).
    """
    model = get_model(model_size)
    segments, info = model.transcribe(
        audio_path,
        task="translate" if translate else "transcribe",
        vad_filter=True,
    )

    seg_list = []
    parts = []
    # `segments` is a lazy generator — iterating it is what actually runs transcription.
    for s in segments:
        text = s.text.strip()
        seg_list.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": text})
        parts.append(text)

    return {
        "text": " ".join(parts).strip(),
        "language": info.language,
        "duration": info.duration,
        "segments": seg_list,
    }
