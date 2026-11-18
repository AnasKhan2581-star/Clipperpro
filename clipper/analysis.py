"""Signal extraction: loudness, loudness rise, chat activity and scene cuts.

All signals are sampled at 1 Hz and returned as numpy arrays of equal length
(one value per second of VOD) so they can be combined into a hype score.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Callable

import numpy as np

SAMPLE_RATE = 8000  # plenty for loudness; keeps 8h VODs cheap to scan


def loudness_per_second(media: Path, duration: float | None = None,
                        progress: Callable[[float], None] | None = None) -> np.ndarray:
    """Stream decoded mono audio from ffmpeg and return RMS loudness (dBFS) per second."""
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", str(media), "-vn", "-ac", "1",
           "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert proc.stdout is not None
    chunk_bytes = SAMPLE_RATE * 2 * 60  # one minute at a time
    values: list[np.ndarray] = []
    leftover = b""
    seconds = 0
    while True:
        data = proc.stdout.read(chunk_bytes)
        if not data:
            break
        data = leftover + data
        usable = len(data) - (len(data) % (SAMPLE_RATE * 2))
        leftover = data[usable:]
        if usable == 0:
            continue
        samples = np.frombuffer(data[:usable], dtype=np.int16).astype(np.float32) / 32768.0
        frames = samples.reshape(-1, SAMPLE_RATE)
        rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
        values.append(20 * np.log10(rms + 1e-9))
        seconds += frames.shape[0]
        if progress and duration:
            progress(min(seconds / duration, 1.0))
    proc.wait()
    if leftover and len(leftover) >= SAMPLE_RATE:  # trailing partial second (>=0.5s)
        samples = np.frombuffer(leftover[: len(leftover) - len(leftover) % 2], dtype=np.int16).astype(np.float32) / 32768.0
        values.append(np.array([20 * np.log10(np.sqrt(np.mean(samples ** 2) + 1e-12) + 1e-9)]))
    if not values:
        raise RuntimeError("No audio could be decoded from the source.")
    return np.concatenate(values)


def rolling_median(x: np.ndarray, window: int) -> np.ndarray:
    window = max(3, int(window) | 1)
    if len(x) < window:
        return np.full_like(x, np.median(x))
    pad = window // 2
    padded = np.pad(x, pad, mode="reflect")
    # compute on a stride to stay fast on long VODs, then interpolate
    step = max(1, window // 30)
    idx = np.arange(0, len(x), step)
    view = np.lib.stride_tricks.sliding_window_view(padded, window)
    med = np.median(view[idx], axis=1)
    return np.interp(np.arange(len(x)), idx, med)


def robust_z(x: np.ndarray) -> np.ndarray:
    if len(x) == 0:
        return x
    med = np.median(x)
    mad = np.median(np.abs(x - med)) * 1.4826
    std = mad if mad > 1e-6 else (np.std(x) or 1.0)
    return (x - med) / std


def smooth(x: np.ndarray, seconds: float) -> np.ndarray:
    w = max(1, int(round(seconds)))
    if w <= 1 or len(x) < w:
        return x.astype(float)
    kernel = np.ones(w) / w
    return np.convolve(x, kernel, mode="same")


def loudness_signals(db: np.ndarray, baseline_seconds: float) -> tuple[np.ndarray, np.ndarray]:
    """Return (relative loudness z, loudness rise z)."""
    # silence floor so muted sections don't skew the statistics
    db = np.maximum(db, -70.0)
    baseline = rolling_median(db, int(baseline_seconds))
    rel = db - baseline
    loud_z = robust_z(smooth(rel, 2))
    rise = np.diff(smooth(db, 3), prepend=db[0])
    rise = np.convolve(np.maximum(rise, 0), np.ones(3), mode="same")
    rise_z = robust_z(rise)
    return loud_z, rise_z


def chat_signal(times: list[float], length: int) -> np.ndarray:
    if not times or length == 0:
        return np.zeros(length)
    counts = np.bincount(np.clip(np.asarray(times, dtype=int), 0, length - 1), minlength=length).astype(float)
    counts = smooth(counts, 5)
    # chat reacts a few seconds AFTER the moment: shift the signal earlier
    lag = 4
    counts = np.concatenate([counts[lag:], np.zeros(lag)])
    baseline = rolling_median(counts, 300)
    return robust_z(counts - baseline)


_SCENE_RE = re.compile(r"pts_time:([0-9.]+)")


def scene_signal(media: Path, length: int, threshold: float = 0.35) -> np.ndarray:
    """Mark visual cuts using ffmpeg's scene score on a 1 fps, low-res decode."""
    cmd = ["ffmpeg", "-v", "info", "-nostdin", "-skip_frame", "nokey", "-i", str(media), "-an",
           "-vf", f"fps=1,scale=160:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    sig = np.zeros(length)
    for m in _SCENE_RE.finditer(proc.stderr):
        t = int(float(m.group(1)))
        if 0 <= t < length:
            sig[t] += 1.0
    return robust_z(smooth(sig, 10)) if sig.any() else sig


def resample_for_chart(x: np.ndarray, points: int = 600) -> list[float]:
    if len(x) == 0:
        return []
    if len(x) <= points:
        return [round(float(v), 3) for v in x]
    edges = np.linspace(0, len(x), points + 1).astype(int)
    return [round(float(x[a:b].max()), 3) for a, b in zip(edges[:-1], edges[1:])]
