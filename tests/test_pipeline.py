import json
import subprocess

from clipper import pipeline


def _probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height",
                          "-show_entries", "format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_end_to_end_local_file(sample_video, tmp_path):
    overrides = {
        "clips": {"count": 1, "min_seconds": 15, "max_seconds": 25, "target_seconds": 20},
        "captions": {"enabled": False},
        "detection": {"weights": {"keywords": 0}},
        "render": {"layout": "facecam"},
    }
    m = pipeline.run(str(sample_video), tmp_path, overrides, streamer_override="Tester")
    assert len(m["clips"]) == 1
    clip = m["clips"][0]
    assert clip["start"] <= 41 and clip["end"] >= 45, clip  # the hype burst is inside
    p = _probe(tmp_path / clip["file"])
    v = next(s for s in p["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (1080, 1920)
    assert any(s["codec_type"] == "audio" for s in p["streams"])
    assert abs(float(p["format"]["duration"]) - clip["duration"]) < 1.0
    assert (tmp_path / "clips" / "clip_01.ass").exists()  # credit overlay
    assert "Tester" in clip["description"]
    assert m["chart"]["score"] and not (tmp_path / "work").exists()


def test_direct_url_without_range_support_falls_back(sample_video, tmp_path, monkeypatch):
    """Direct links: yt-dlp reports no duration (ffprobe fills it in) and a host that can't
    seek makes section downloads fail (pipeline falls back to one full download)."""
    import threading
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    class Quiet(SimpleHTTPRequestHandler):  # stdlib server: no HTTP Range support
        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(sample_video.parent)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(pipeline, "load_streamers", lambda: {"enforce": False, "streamers": []})
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/{sample_video.name}"
        overrides = {"clips": {"count": 1, "min_seconds": 15, "max_seconds": 25, "target_seconds": 20},
                     "captions": {"enabled": False}, "detection": {"weights": {"keywords": 0}}}
        m = pipeline.run(url, tmp_path, overrides, streamer_override="Tester")
    finally:
        srv.shutdown()
    assert abs(m["info"]["duration"] - 90) < 1
    clip = m["clips"][0]
    assert clip["start"] <= 41 and clip["end"] >= 45
    assert "t=" in clip["source_link"]
    assert (tmp_path / clip["file"]).exists()
