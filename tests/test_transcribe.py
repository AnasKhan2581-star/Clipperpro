"""Whisper integration without downloading a model: checks what we feed it and how we map results."""
from types import SimpleNamespace

import numpy as np

from clipper import transcribe


class FakeModel:
    def __init__(self):
        self.seen = None

    def transcribe(self, audio, **kw):
        self.seen = audio
        w = [SimpleNamespace(start=1.0, end=1.4, word=" no"), SimpleNamespace(start=1.5, end=1.9, word=" way")]
        return iter([SimpleNamespace(start=1.0, end=1.9, text=" no way", words=w)]), None


def test_feeds_raw_samples_not_a_path(sample_video, monkeypatch):
    # Regression: passing a file path makes faster-whisper decode with PyAV, which new
    # PyAV releases break ("unexpected keyword argument 'metadata_errors'").
    fake = FakeModel()
    monkeypatch.setattr(transcribe, "_model", lambda name: fake)
    segs = transcribe.transcribe_window(sample_video, 40.0, 50.0, "tiny")
    assert isinstance(fake.seen, np.ndarray) and fake.seen.dtype == np.float32
    assert abs(len(fake.seen) / 16000 - 10.0) < 0.1  # 10 s at 16 kHz
    assert float(np.abs(fake.seen).max()) > 0.1      # the loud burst (0.125 peak tone) is in there
    assert segs[0]["start"] == 41.0 and segs[0]["words"][1] == {"start": 41.5, "end": 41.9, "word": "way"}
