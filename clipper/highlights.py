"""Turn per-second signals into ranked, well-bounded clip windows."""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

import numpy as np

from .analysis import smooth


@dataclass
class Candidate:
    peak: float
    start: float
    end: float
    score: float
    keyword_hits: int = 0
    final_score: float = 0.0
    hook: str = ""
    words: list = field(default_factory=list)  # [{"start","end","word"}] absolute times
    signals: dict = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("words", None)
        return d


def combine(signals: dict[str, np.ndarray], weights: dict[str, float], smoothing_seconds: float) -> np.ndarray:
    length = max((len(v) for v in signals.values()), default=0)
    total = np.zeros(length)
    for name, sig in signals.items():
        w = float(weights.get(name, 0.0))
        if w == 0 or len(sig) == 0:
            continue
        s = np.zeros(length)
        s[: len(sig)] = np.clip(sig, -3.0, 6.0)
        total += w * s
    return smooth(total, smoothing_seconds)


def pick_candidates(score: np.ndarray, n: int, clip_cfg: dict, duration: float) -> list[Candidate]:
    """Greedy peak picking with non-maximum suppression.

    If the VOD is too short to fit `n` peaks with the configured gap, the gap is
    relaxed down to the max clip length (clips never overlap).
    """
    cands = _pick(score, n, clip_cfg, duration, max(clip_cfg["min_gap_seconds"], clip_cfg["max_seconds"]))
    if len(cands) < n and clip_cfg["min_gap_seconds"] > clip_cfg["max_seconds"]:
        cands = _pick(score, n, clip_cfg, duration, clip_cfg["max_seconds"])
    return cands


def _pick(score: np.ndarray, n: int, clip_cfg: dict, duration: float, gap: float) -> list[Candidate]:
    target = clip_cfg["target_seconds"]
    pre = clip_cfg["pre_roll"]
    duration = float(duration or len(score))
    if len(score) == 0:
        return []
    order = np.argsort(score)[::-1]
    chosen: list[Candidate] = []
    edge = min(5.0, duration / 10)
    for idx in order:
        t = float(idx) + 0.5
        if t < edge or t > duration - edge:
            continue
        if any(abs(t - c.peak) < gap for c in chosen):
            continue
        start = max(0.0, t - pre * target)
        end = min(duration, start + target)
        start = max(0.0, end - target)
        chosen.append(Candidate(peak=t, start=start, end=end, score=float(score[idx])))
        if len(chosen) >= n:
            break
    return chosen


_NUMBERS = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five",
            "6": "six", "7": "seven", "8": "eight", "9": "nine", "10": "ten"}


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation and spell out small numbers (Whisper writes "5 stars")."""
    text = re.sub(r"[^a-z0-9'? ]+", " ", text.lower())
    return re.sub(r"\b(10|[0-9])\b", lambda m: _NUMBERS[m.group(1)], text)


def count_keywords(text: str, keywords: list[str]) -> int:
    text = " " + _normalize(text) + " "
    hits = 0
    for kw in keywords:
        kw = _normalize(kw).strip()
        if not kw:
            continue
        hits += len(re.findall(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", text))
    return hits


def refine_with_transcript(c: Candidate, segments: list[dict], clip_cfg: dict, duration: float) -> None:
    """Snap clip boundaries to sentence boundaries so clips don't start/end mid-word.

    `segments` are absolute-time transcript segments: {"start","end","text","words"}.
    """
    lo, hi = clip_cfg["min_seconds"], clip_cfg["max_seconds"]
    if segments:
        starts = [s["start"] for s in segments if s["start"] <= c.peak - 1.0]
        # latest sentence start that still leaves room before the peak
        good_starts = [s for s in starts if c.start - 6.0 <= s <= c.start + 6.0]
        if good_starts:
            c.start = min(good_starts, key=lambda s: abs(s - c.start))
        ends = [s["end"] for s in segments if s["end"] >= c.peak + 1.0]
        good_ends = [e for e in ends if c.end - 6.0 <= e <= c.end + 8.0]
        if good_ends:
            c.end = min(good_ends, key=lambda e: abs(e - c.end)) + 0.4
    # enforce length limits around the peak
    if c.end - c.start > hi:
        c.end = c.start + hi
    if c.end - c.start < lo:
        missing = lo - (c.end - c.start)
        c.start = max(0.0, c.start - missing * 0.4)
        c.end = min(duration, c.start + lo)
    c.start = round(max(0.0, c.start), 2)
    c.end = round(min(duration, c.end), 2)
    c.words = [w for s in segments for w in s.get("words", []) if c.start <= w["start"] < c.end]


def pick_hook(c: Candidate, segments: list[dict], keywords: list[str]) -> str:
    inside = [s for s in segments if s["end"] > c.start and s["start"] < c.end and s["text"].strip()]
    if not inside:
        return ""
    best = max(inside, key=lambda s: (count_keywords(s["text"], keywords), -abs(s["start"] - c.peak)))
    text = re.sub(r"\s+", " ", best["text"]).strip().strip("-").strip()
    if len(text) > 60:
        text = text[:57].rsplit(" ", 1)[0] + "..."
    return text


def finalize(cands: list[Candidate], count: int, kw_weight: float) -> list[Candidate]:
    for c in cands:
        c.final_score = round(c.score + kw_weight * min(c.keyword_hits, 6) * 0.5, 3)
    ranked = sorted(cands, key=lambda c: c.final_score, reverse=True)[:count]
    return ranked
