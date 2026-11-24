"""Instagram Reels upload against a fake Meta Graph API (no network, no real account)."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from clipper import instagram


class FakeMeta:
    """Just enough of graph.facebook.com + rupload.facebook.com for the publish flow."""

    def __init__(self):
        self.calls, self.uploaded, self.polls = [], b"", 0
        self.fail_token = False
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body):
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _params(self):
                q = parse_qs(urlparse(self.path).query)
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                return q, body

            def do_GET(self):
                q, _ = self._params()
                p = urlparse(self.path).path
                fake.calls.append(("GET", p, {k: v[0] for k, v in q.items()}))
                tok = q.get("access_token", [""])[0]
                if fake.fail_token:
                    return self._send(400, {"error": {"message": "Error validating access token", "code": 190}})
                if p.endswith("/oauth/access_token"):
                    if q.get("client_secret") != ["s3cret-0123456789abcdef"]:
                        return self._send(400, {"error": {"message": "Invalid client_secret", "code": 1}})
                    return self._send(200, {"access_token": "LONG_USER", "token_type": "bearer"})
                if p.endswith("/me/accounts"):
                    assert tok == "LONG_USER"
                    return self._send(200, {"data": [
                        {"id": "P0", "name": "Personal page", "access_token": "X"},
                        {"id": "P1", "name": "TomoClips", "access_token": "PAGE_TOKEN",
                         "instagram_business_account": {"id": "IG1", "username": "tomoclips"}}]})
                if p.endswith("/C1"):
                    fake.polls += 1
                    return self._send(200, {"status_code": "FINISHED" if fake.polls >= 2 else "IN_PROGRESS"})
                if p.endswith("/M1"):
                    return self._send(200, {"permalink": "https://www.instagram.com/reel/abc123/"})
                if p.endswith("/content_publishing_limit"):
                    return self._send(200, {"data": [{"quota_usage": 1, "config": {"quota_total": 50}}]})
                self._send(404, {"error": {"message": "unknown " + p}})

            def do_POST(self):
                q, body = self._params()
                p = urlparse(self.path).path
                form = {k: v[0] for k, v in parse_qs(body.decode(errors="replace")).items()} if b"=" in body[:200] else {}
                fake.calls.append(("POST", p, form or dict(self.headers)))
                if p.endswith("/IG1/media"):
                    assert form["media_type"] == "REELS" and form["upload_type"] == "resumable"
                    assert form["access_token"] == "PAGE_TOKEN"
                    fake.caption = form["caption"]
                    return self._send(200, {"id": "C1", "uri": f"{fake.base}/rupload/v27.0/C1"})
                if p.endswith("/rupload/v27.0/C1"):
                    assert self.headers["Authorization"] == "OAuth PAGE_TOKEN"
                    assert self.headers["offset"] == "0" and int(self.headers["file_size"]) == len(body)
                    fake.uploaded = body
                    return self._send(200, {"success": True, "message": "Upload successful."})
                if p.endswith("/IG1/media_publish"):
                    assert form["creation_id"] == "C1"
                    return self._send(200, {"id": "M1"})
                self._send(404, {"error": {"message": "unknown " + p}})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


@pytest.fixture
def meta(monkeypatch):
    fake = FakeMeta()
    monkeypatch.setattr(instagram, "GRAPH", fake.base)
    monkeypatch.setattr(instagram, "POLL_SECONDS", 0.01)
    instagram.disconnect()
    yield fake
    fake.srv.shutdown()
    instagram.disconnect()


def test_connect_finds_the_linked_instagram_account(meta):
    st = instagram.connect(" 123456 ", "s3cret-0123456789abcdef", "SHORT_TOKEN_FROM_EXPLORER")
    assert st["connected"] and st["username"] == "tomoclips" and st["page"] == "TomoClips"
    saved = json.loads(instagram.CONFIG.read_text())
    assert saved["page_token"] == "PAGE_TOKEN" and saved["ig_user_id"] == "IG1" and saved["user_token"] == "LONG_USER"
    ex = next(c for c in meta.calls if c[1].endswith("/oauth/access_token"))[2]
    assert ex["grant_type"] == "fb_exchange_token" and ex["client_id"] == "123456"


def test_connect_explains_problems(meta):
    with pytest.raises(instagram.InstagramError, match="Invalid client_secret"):
        instagram.connect("123456", "wrong", "SHORT_TOKEN_FROM_EXPLORER")
    assert not instagram.status()["connected"]


def test_upload_publishes_a_reel(meta, tmp_path):
    instagram.connect("123456", "s3cret-0123456789abcdef", "SHORT_TOKEN_FROM_EXPLORER")
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"x" * 50_000)
    steps = []
    res = instagram.upload(video, "Funny moment #tomovids #gta6", progress=steps.append)
    assert res == {"video_id": "M1", "url": "https://www.instagram.com/reel/abc123/"}
    assert meta.uploaded == video.read_bytes()
    assert meta.caption == "Funny moment #tomovids #gta6" and meta.polls == 2
    assert any("processing" in s for s in steps)
    assert instagram.publishing_limit()["quota_usage"] == 1


def test_expired_token_says_reconnect(meta, tmp_path):
    instagram.connect("123456", "s3cret-0123456789abcdef", "SHORT_TOKEN_FROM_EXPLORER")
    meta.fail_token = True
    with pytest.raises(instagram.InstagramError, match="reconnect Instagram"):
        instagram.publishing_limit() or instagram._graph("GET", "C1", "PAGE_TOKEN")


def test_caption_keeps_30_hashtags_and_2200_chars():
    tags = " ".join(f"#tag{i}" for i in range(40))
    cap = instagram.caption_for({"description": "Hook 😂\nSubscribe to Tomo 👉 @Tomovids\n\n" + tags})
    assert cap.count("#") == 30 and "#tag29" in cap and "#tag30" not in cap and "@Tomovids" in cap
    long = instagram.caption_for({"description": "word " * 1000})
    assert len(long) <= 2200 and long.endswith("…")


def test_instagram_button_flow_via_api(meta, sample_video):
    from fastapi.testclient import TestClient

    from clipper.server import app
    client = TestClient(app)
    assert not client.get("/api/config").json()["instagram"]["connected"]
    r = client.post("/api/instagram/connect", json={"app_id": "123456", "app_secret": "s3cret-0123456789abcdef",
                                                     "user_token": "SHORT_TOKEN_FROM_EXPLORER"})
    assert r.status_code == 200 and r.json()["username"] == "tomoclips"
    assert client.get("/api/config").json()["instagram"]["connected"]

    opts = '{"count":1,"captions":false,"layout":"blur","min_seconds":15,"max_seconds":20}'
    with sample_video.open("rb") as fh:
        job_id = client.post("/api/jobs/upload", files={"file": ("vod.mp4", fh, "video/mp4")},
                             data={"options": opts}).json()["id"]
    for _ in range(240):
        if client.get(f"/api/jobs/{job_id}").json()["status"] in ("done", "failed"):
            break
        time.sleep(0.5)
    assert client.post(f"/api/jobs/{job_id}/clips/clip_01/instagram").status_code == 200
    for _ in range(60):
        ups = [u for u in client.get(f"/api/jobs/{job_id}").json()["uploads"] if u["platform"] == "instagram"]
        if ups and ups[-1]["status"] in ("done", "failed"):
            break
        time.sleep(0.25)
    assert ups[-1]["status"] == "done", ups[-1].get("error")
    assert ups[-1]["url"] == "https://www.instagram.com/reel/abc123/"
    assert client.get("/api/config").json()["instagram"]["uploads_24h"] == 1
    assert client.get("/api/config").json()["youtube"]["uploads_24h"] == 0     # counted separately

    assert client.delete("/api/instagram").json()["connected"] is False
    assert client.post(f"/api/jobs/{job_id}/clips/clip_01/instagram").status_code == 400
