import time

from fastapi.testclient import TestClient

from clipper.server import app

client = TestClient(app)


def test_health_and_config():
    assert client.get("/api/health").json()["ok"]
    c = client.get("/api/config").json()
    assert "blur" in c["layouts"] and c["defaults"]["count"] >= 1


def test_index_served():
    r = client.get("/")
    assert r.status_code == 200 and "Clip Pipeline" in r.text


def test_streamers_roundtrip():
    body = {"enforce": True, "streamers": [{"name": "Tester", "match": ["tester"], "credit_url": "https://twitch.tv/tester", "permission": "ok"}]}
    assert client.put("/api/streamers", json=body).status_code == 200
    got = client.get("/api/streamers").json()
    assert got["streamers"][0]["name"] == "Tester"


def test_rejects_bad_input():
    assert client.post("/api/jobs", json={"url": "not a url"}).status_code == 400
    assert client.post("/api/jobs", json={"url": "https://x.y/z", "layout": "nope"}).status_code == 400
    assert client.get("/media/..%2f/clips/x").status_code == 404
    assert client.patch("/api/jobs/abc/clips/evil.json", json={}).status_code == 400
    assert client.patch("/api/jobs/abc/clips/../../etc", json={}).status_code in (400, 404, 405)


def test_upload_job_runs(sample_video):
    opts = '{"count":1,"captions":false,"layout":"blur","min_seconds":15,"max_seconds":20}'
    with sample_video.open("rb") as fh:
        r = client.post("/api/jobs/upload", files={"file": ("vod.mp4", fh, "video/mp4")}, data={"options": opts})
    assert r.status_code == 200, r.text
    job_id = r.json()["id"]
    for _ in range(240):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed"):
            break
        time.sleep(0.5)
    assert job["status"] == "done", job.get("error")
    assert job["title"] == "vod.mp4"
    clip = job["manifest"]["clips"][0]
    assert client.get(f"/media/{job_id}/clips/{clip['name']}.mp4").status_code == 200
    r = client.patch(f"/api/jobs/{job_id}/clips/{clip['name']}", json={"title": "Edited"})
    assert r.json()["title"] == "Edited"
    assert client.get(f"/api/jobs/{job_id}").json()["manifest"]["clips"][0]["title"] == "Edited"
