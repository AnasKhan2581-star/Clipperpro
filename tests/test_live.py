"""Live mode: chunk planning, recording a (fake) live stream, and clipping its chunks."""
import time
from pathlib import Path

import pytest

from clipper import live, pipeline

CFG = {**live.DEFAULTS, "chunk_minutes": 20, "context_seconds": 60, "min_final_seconds": 120}
TESTER = {"enforce": True, "streamers": [{"name": "Tester", "match": ["tester"],
                                          "credit_url": "https://www.youtube.com/@tester"}]}


def segs(start, n, dur=60.0):
    return [live.Segment(Path(f"s{start + i * dur:.0f}.ts"), start + i * dur, dur) for i in range(n)]


# ------------------------------------------------------------------ planning
def test_waits_until_a_full_chunk_exists():
    assert live.plan_chunk(segs(0, 20), None, CFG, final=False) is None   # 20 min, needs 20 min + TAIL


def test_chunks_cover_every_moment_exactly_once():
    s = segs(1000, 65)                   # 65 minutes of stream starting at 0:16:40
    cursor, windows = None, []
    while (p := live.plan_chunk(s, cursor, CFG, final=False)) is not None:
        windows.append((p.offset + p.peak_from, p.offset + p.peak_to))
        assert p.segments[0].start <= p.offset + p.peak_from - (CFG["context_seconds"] if cursor else 0)
        cursor = p.next_cursor
    p = live.plan_chunk(s, cursor, CFG, final=True)       # stream ended: the rest
    windows.append((p.offset + p.peak_from, p.end))
    assert p.peak_to is None
    assert windows[0][0] == 1000 and windows[-1][1] == s[-1].end
    for (a0, a1), (b0, b1) in zip(windows, windows[1:]):
        assert a1 == b0, windows                          # no gaps, no overlap between windows
    assert all(b - a >= 20 * 60 for a, b in windows[:-1])


def test_short_leftover_at_the_end_is_skipped_but_a_closed_run_is_clipped():
    s = segs(0, 21)                                       # 21 min: one chunk; 30 s + 0 left over
    p = live.plan_chunk(s, None, CFG, final=False)
    assert p is not None and p.end == 21 * 60
    assert live.plan_chunk(s, p.next_cursor, CFG, final=True) is None   # 30 s leftover < 120 s

    gap = segs(0, 5) + segs(600, 3)                       # recorder dropped for 5 min, then resumed
    p = live.plan_chunk(gap, None, CFG, final=False)
    assert p.end == 300 and p.peak_to is None             # the closed first run is clipped to its end
    assert live.plan_chunk(gap, p.next_cursor, CFG, final=False) is None   # 3 new min: wait


def test_overlapping_segments_after_reconnect_are_trimmed():
    a = live.Segment(Path("r001_00000.ts"), 0, 60)
    b = live.Segment(Path("r002_00000.ts"), 52, 60)       # recorder restarted 8 s back
    text = live.concat_list([a, b])
    assert "r001_00000.ts" in text and "inpoint 8.000" in text


def test_record_cmd_maps_youtube_and_muxed_streams(tmp_path):
    yt = live.record_cmd(["https://v", "https://a"], {"User-Agent": "x"}, tmp_path, 2, 60)
    assert yt[yt.index("-copyts")] and "1:a:0" in yt and yt.count("-headers") == 2
    assert yt[-1].endswith("r002_%05d.ts")
    tw = live.record_cmd(["https://muxed"], {}, tmp_path, 1, 60)
    assert "0:a:0?" in tw and "-headers" not in tw


# ------------------------------------------------------------------ recording
def _fake_stream(monkeypatch, sample_video, calls):
    """The 'live stream' is the sample video: ffmpeg records it at full speed and exits,
    then the stream reports it has ended."""
    def probe(self):
        calls["probe"] += 1
        return {"id": "abc", "title": "Test stream", "uploader": "Tester", "is_live": calls["probe"] == 1,
                "channel_url": "https://www.youtube.com/@tester", "webpage_url": "https://www.youtube.com/watch?v=abc",
                "is_local": False, "release_timestamp": None}
    monkeypatch.setattr(live.LiveSession, "probe", probe)
    monkeypatch.setattr(live.LiveSession, "resolve", lambda self: ([str(sample_video)], {}))
    monkeypatch.setattr(live, "load_streamers", lambda: TESTER)
    monkeypatch.setattr(pipeline, "load_streamers", lambda: TESTER)
    small = {"chunk_minutes": 0.5, "segment_seconds": 10, "context_seconds": 10, "min_final_seconds": 20,
             "poll_seconds": 0.2, "clips_per_chunk": 1}
    monkeypatch.setattr(live, "live_config", lambda s: {**live.DEFAULTS, **small})


def test_session_records_and_clips_a_live_stream(sample_video, tmp_path, monkeypatch):
    calls = {"probe": 0}
    _fake_stream(monkeypatch, sample_video, calls)
    sent = []
    s = live.LiveSession("https://www.youtube.com/watch?v=abc", tmp_path / "rec", {}, lambda p, o: sent.append((p, o)))
    res = s.run()
    assert res["ended"] and res["chunks"] == len(sent) == 2
    assert not list((tmp_path / "rec" / "segments").glob("*.ts"))           # recording cleaned up
    (c1, o1), (c2, o2) = sent
    assert o1["peak_from"] == 0 and o2["peak_to"] is None
    assert abs((o1["offset"] + o1["peak_to"]) - (o2["offset"] + o2["peak_from"])) < 0.01  # windows meet
    assert abs(live.probe_segment(c2)[1] - (90 - o2["offset"] + 1.4)) < 2.5     # chunk = rest of the stream

    # the hype burst (40-46 s into the stream) is in chunk 2's window; clip it like the worker does
    overrides = {"clips": {"count": 1, "min_seconds": 15, "max_seconds": 25, "target_seconds": 20},
                 "captions": {"enabled": False}, "detection": {"weights": {"keywords": 0}}}
    m = pipeline.run(str(c2), tmp_path / "out", overrides, live=o2)
    clip = m["clips"][0]
    assert clip["stream_start"] <= 41.5 and clip["stream_start"] + clip["duration"] >= 45, clip
    assert f"t={int(clip['stream_start'])}s" in clip["source_link"]
    assert m["streamer"]["name"] == "Tester" and "Test stream" == m["info"]["title"]


def test_peak_window_excludes_moments_owned_by_another_chunk(sample_video, tmp_path):
    overrides = {"clips": {"count": 1, "min_seconds": 15, "max_seconds": 25, "target_seconds": 20},
                 "captions": {"enabled": False}, "detection": {"weights": {"keywords": 0}}}
    m = pipeline.run(str(sample_video), tmp_path, overrides, streamer_override="Tester",
                     live={"offset": 100, "peak_from": 50, "peak_to": None})
    clip = m["clips"][0]
    assert clip["peak"] >= 50 and clip["stream_start"] == pytest.approx(clip["start"] + 100)


# ------------------------------------------------------------------ web API
def test_live_session_via_api(sample_video, monkeypatch):
    from fastapi.testclient import TestClient

    from clipper.server import app
    calls = {"probe": 0}
    _fake_stream(monkeypatch, sample_video, calls)
    client = TestClient(app)
    r = client.post("/api/jobs", json={"url": "https://www.youtube.com/watch?v=abc", "live": True, "count": 1,
                                       "chunk_minutes": 5, "captions": False, "layout": "blur",
                                       "min_seconds": 15, "max_seconds": 20})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    for _ in range(120):
        session = client.get(f"/api/jobs/{sid}").json()
        if session["status"] != "running":
            break
        time.sleep(0.5)
    assert session["status"] == "done", session.get("error")
    assert session["title"].startswith("🔴 LIVE") and "2 chunk" in session["message"]
    chunks = [j for j in client.get("/api/jobs").json() if j["options"].get("live_session") == sid]
    assert len(chunks) == 2
    for _ in range(240):
        states = [client.get(f"/api/jobs/{j['id']}").json() for j in chunks]
        if all(s["status"] in ("done", "failed") for s in states):
            break
        time.sleep(0.5)
    assert all(s["status"] == "done" for s in states), [s.get("error") for s in states]
    clips = [c for s in states for c in (s["manifest"] or {}).get("clips", [])]
    assert clips and all("stream_start" in c for c in clips)
    assert not any(Path(j["source"]).exists() for j in chunks)        # chunk files deleted once clipped
