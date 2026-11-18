"""Local, free speech-to-text with faster-whisper (word-level timestamps)."""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import threading
from pathlib import Path

log = logging.getLogger(__name__)

_models: dict[str, object] = {}
_lock = threading.Lock()


def _model(name: str):
    with _lock:
        if name not in _models:
            from faster_whisper import WhisperModel

            device = os.environ.get("WHISPER_DEVICE", "cpu")
            compute = "int8" if device == "cpu" else "float16"
            threads = int(os.environ.get("WHISPER_THREADS", "0")) or max(1, (os.cpu_count() or 2))
            log.info("loading whisper model %s on %s", name, device)
            _models[name] = WhisperModel(name, device=device, compute_type=compute, cpu_threads=threads)
        return _models[name]


def extract_wav(media: Path, start: float, end: float, out: Path) -> Path:
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", f"{max(0.0, start):.3f}", "-i", str(media),
           "-t", f"{max(0.1, end - start):.3f}", "-vn", "-ac", "1", "-ar", "16000", str(out)]
    subprocess.run(cmd, check=True)
    return out


def transcribe_window(media: Path, start: float, end: float, model: str = "base",
                      language: str | None = None) -> list[dict]:
    """Transcribe media[start:end]. Returns segments with ABSOLUTE timestamps."""
    start = max(0.0, start)
    with tempfile.TemporaryDirectory() as tmp:
        wav = extract_wav(media, start, end, Path(tmp) / "w.wav")
        segments, _info = _model(model).transcribe(
            str(wav),
            language=language or None,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 400},
            beam_size=1,
            condition_on_previous_text=False,
        )
        out = []
        for seg in segments:
            words = [
                {"start": round(start + w.start, 3), "end": round(start + w.end, 3), "word": w.word.strip()}
                for w in (seg.words or []) if w.word.strip()
            ]
            out.append({
                "start": round(start + seg.start, 3),
                "end": round(start + seg.end, 3),
                "text": seg.text.strip(),
                "words": words,
            })
        return out
