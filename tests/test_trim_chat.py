"""Trim/re-cut a clip, and the chat signal (live chat recording + noise-aware scoring)."""
import json
import socket
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from clipper import analysis, live, livechat, pipeline

OVR = {"clips": {"count": 1, "min_seconds": 15, "max_seconds": 25, "target_seconds": 20},
       "captions": {"enabled": False}, "detection": {"weights": {"keywords": 0}}}


def _probe_dur(p):
    return live.probe_segment(p)[1]


# ------------------------------------------------------------------ trim
def test_recut_extends_a_clip_and_keeps_the_title(sample_video, tmp_path):
    m = pipeline.run(str(sample_video), tmp_path, OVR, streamer_override="Tester")
    clip = m["clips"][0]
    p = tmp_path / "clips" / "clip_01.json"
    edited = {**json.loads(p.read_text()), "title": "My own title"}
    p.write_text(json.dumps(edited))

    new = pipeline.rerender_clip(tmp_path, "clip_01", 30.0, 72.0)     # 42 s: longer than max_seconds
    assert new["title"] == "My own title"
    assert (new["start"], new["end"], new["duration"]) == (30.0, 72.0, 42.0)
    assert new["rendered_at"] > 0 and new["trimmed"]
    assert abs(_probe_dur(tmp_path / "clips" / "clip_01.mp4") - 42) < 1.0
    assert json.loads(p.read_text())["duration"] == 42.0
    assert clip["duration"] <= 25                                         # it really got longer


@pytest.mark.parametrize("a,b", [(10, 11), (0, 200), (-5, 20)])
def test_recut_rejects_bad_lengths(sample_video, tmp_path, a, b):
    pipeline.run(str(sample_video), tmp_path, OVR, streamer_override="Tester")
    with pytest.raises(pipeline.PipelineError):
        pipeline.rerender_clip(tmp_path, "clip_01", a, b)


def test_recut_explains_when_the_uploaded_file_is_gone(sample_video, tmp_path):
    src = tmp_path / "upload.mp4"
    src.write_bytes(sample_video.read_bytes())
    out = tmp_path / "job"
    pipeline.run(str(src), out, OVR, streamer_override="Tester")
    src.unlink()
    with pytest.raises(pipeline.PipelineError, match="Upload it again"):
        pipeline.rerender_clip(out, "clip_01", 10, 40)


def test_trim_api_validates_and_reports_failures(sample_video):
    from fastapi.testclient import TestClient

    from clipper.server import app
    client = TestClient(app)
    opts = '{"count":1,"captions":false,"layout":"blur","min_seconds":15,"max_seconds":20}'
    with sample_video.open("rb") as fh:
        job_id = client.post("/api/jobs/upload", files={"file": ("vod.mp4", fh, "video/mp4")},
                             data={"options": opts}).json()["id"]
    for _ in range(240):
        if client.get(f"/api/jobs/{job_id}").json()["status"] in ("done", "failed"):
            break
        time.sleep(0.5)
    assert client.post(f"/api/jobs/{job_id}/clips/clip_01/trim", json={"start": 10, "end": 300}).status_code == 400
    assert client.post(f"/api/jobs/{job_id}/clips/evil/trim", json={"start": 10, "end": 30}).status_code == 400
    assert client.post(f"/api/jobs/{job_id}/clips/clip_01/trim", json={"start": 10, "end": 40}).status_code == 200
    for _ in range(60):
        job = client.get(f"/api/jobs/{job_id}").json()
        msg = job["message"] or ""
        if job["status"] == "done" and ("re-cut failed" in msg or "re-cut to" in msg):
            break
        time.sleep(0.5)
    # the upload was deleted after clipping, so the re-cut fails with a clear message; the job stays usable
    assert job["status"] == "done" and "Upload it again" in job["message"], job["message"]
    assert job["manifest"]["clips"]


# ------------------------------------------------------------------ chat signal
def test_quiet_chat_noise_is_not_a_spike_but_a_burst_is():
    rng = np.random.default_rng(1)
    n = 1800
    quiet = list(np.sort(rng.uniform(0, n, 450)))           # ~15 msgs/min, random
    z = analysis.chat_signal(quiet, n)
    assert z.max() < 4.5, z.max()
    burst = quiet + list(rng.uniform(900, 906, 40))          # 40 messages in 6 s
    zb = analysis.chat_signal(burst, n)
    assert zb.max() > 8 and 890 <= int(np.argmax(zb)) <= 910


def test_chat_maps_to_stream_time_through_segment_clock(tmp_path):
    s = live.LiveSession("u", tmp_path, {}, lambda p, o: None)
    segs = [live.Segment(Path(f"r001_{i:05d}.ts"), 100 + i * 10, 10, wall_end=5000 + 100 + (i + 1) * 10 + 0.3)
            for i in range(6)]
    assert s._wall_shift(segs) == pytest.approx(5000.3)

    class Fake(livechat.ChatRecorder):
        pass
    s.chat = Fake()
    for t in (5000.3 + 125, 5000.3 + 125.5, 5000.3 + 90, 5000.3 + 200):   # 2 inside, 2 outside the chunk
        s.chat.add(t)
    plan = live.ChunkPlan(segments=segs[1:4], offset=110, peak_from=0, peak_to=None, next_cursor=140)
    f = s._chat_file(plan, segs, tmp_path / "chunk_001.mp4")
    assert json.loads(Path(f).read_text()) == [15.0, 15.5]   # seconds into the chunk


def test_youtube_live_chat_file_parsing(tmp_path):
    rec = livechat.YouTubeChat("u", tmp_path)
    f = tmp_path / "chat.live_chat.json.part"
    msg = '{"replayChatItemAction": {"actions": [{"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {"timestampUsec": "%d"}}}}]}, "videoOffsetTimeMsec": "0", "isLive": true}\n'
    paid = '{"replayChatItemAction": {"actions": [{"addChatItemAction": {"item": {"liveChatPaidMessageRenderer": {"timestampUsec": "1795000000000000"}}}}]}}\n'
    f.write_text(msg % 1795000001000000 + paid + msg % 1795000002500000 + '{"partial')
    assert rec.times_between(0, 2e9) == [1795000001.0, 1795000002.5]   # paid + partial line skipped
    with f.open("a") as fh:
        fh.write('": 1}\n' + msg % 1795000003000000)
    assert rec.count == 3


def test_twitch_chat_reads_messages_over_irc(monkeypatch):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    got = {}

    def serve():
        conn, _ = srv.accept()
        got["hello"] = conn.recv(1024)
        conn.sendall(b"PING :tmi.twitch.tv\r\n"
                     b":a!a@a.tmi.twitch.tv PRIVMSG #kai :W\r\n:b!b@b.tmi.twitch.tv PRIVMSG #kai :LMAO\r\n")
        got["pong"] = conn.recv(1024)
        time.sleep(0.5)
        conn.close()
    threading.Thread(target=serve, daemon=True).start()
    monkeypatch.setattr(livechat.TwitchChat, "HOST", "127.0.0.1")
    monkeypatch.setattr(livechat.TwitchChat, "PORT", srv.getsockname()[1])
    rec = livechat.TwitchChat("Kai")
    rec.start()
    for _ in range(40):
        if rec.count >= 2:
            break
        time.sleep(0.1)
    rec.stop()
    assert rec.count == 2
    assert b"JOIN #kai" in got["hello"] and b"NICK justinfan" in got["hello"]
    assert got["pong"].startswith(b"PONG :tmi.twitch.tv")


def test_recorder_choice():
    assert isinstance(livechat.recorder_for({"extractor": "youtube"}, "https://youtu.be/x", Path("."), {}),
                      livechat.YouTubeChat)
    tw = livechat.recorder_for({"extractor": "twitchstream"}, "https://www.twitch.tv/KaiCenat", Path("."), {})
    assert isinstance(tw, livechat.TwitchChat) and tw.channel == "kaicenat"
    assert livechat.recorder_for({"extractor": "kick"}, "https://kick.com/x", Path("."), {}) is None
