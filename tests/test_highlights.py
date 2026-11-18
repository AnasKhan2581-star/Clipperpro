import numpy as np

from clipper import highlights
from clipper.config import validate

CFG = validate({"clips": {"count": 3, "min_seconds": 20, "max_seconds": 40, "target_seconds": 30,
                          "pre_roll": 0.6, "min_gap_seconds": 90}})["clips"]


def test_pick_candidates_finds_peaks_with_gap():
    score = np.zeros(1000)
    for t, v in [(100, 9), (110, 8), (500, 7), (800, 6)]:
        score[t] = v
    c = highlights.pick_candidates(score, 3, CFG, 1000)
    assert [round(x.peak) for x in c] == [100, 500, 800]  # 110 suppressed by gap
    first = c[0]
    assert first.end - first.start == 30
    assert first.start < first.peak < first.end
    assert abs((first.peak - first.start) - 18) < 1  # 60% pre-roll


def test_gap_relaxes_for_short_vods():
    score = np.zeros(200)
    score[[50, 100, 150]] = [5, 4, 3]
    c = highlights.pick_candidates(score, 3, CFG, 200)
    assert len(c) == 3
    spans = sorted((x.start, x.end) for x in c)
    assert all(a[1] <= b[0] + 1e-6 or b[0] - a[0] >= CFG["max_seconds"] for a, b in zip(spans, spans[1:]))


def test_keyword_count_is_word_bounded():
    kws = ["no way", "run", "cops"]
    assert highlights.count_keywords("NO WAY bro, run! the cops, the COPS", kws) == 4
    assert highlights.count_keywords("running brunch", kws) == 0


def test_refine_snaps_to_sentences_and_respects_limits():
    c = highlights.Candidate(peak=118, start=100, end=130, score=5)
    segs = [
        {"start": 97.0, "end": 104.0, "text": "okay so", "words": [{"start": 97.0, "end": 97.5, "word": "okay"}]},
        {"start": 105.0, "end": 119.0, "text": "no way", "words": [{"start": 110.0, "end": 110.4, "word": "no"}]},
        {"start": 120.0, "end": 132.5, "text": "lets go", "words": [{"start": 131.0, "end": 131.4, "word": "go"}]},
    ]
    highlights.refine_with_transcript(c, segs, CFG, 1000)
    assert c.start == 97.0
    assert c.end == 132.9
    assert CFG["min_seconds"] <= c.end - c.start <= CFG["max_seconds"]
    assert [w["word"] for w in c.words] == ["okay", "no", "go"]


def test_finalize_prefers_keywords():
    a = highlights.Candidate(peak=1, start=0, end=30, score=5.0, keyword_hits=0)
    b = highlights.Candidate(peak=2, start=0, end=30, score=4.5, keyword_hits=4)
    assert highlights.finalize([a, b], 1, 0.8)[0] is b
