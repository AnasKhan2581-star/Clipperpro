"""FastAPI web app: dashboard + REST API."""
from __future__ import annotations

import base64
import os
import re
import secrets
import shutil
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, youtube
from .config import (DATA_DIR, LAYOUTS, ROOT, WHISPER_MODELS, load_settings, load_streamers,
                     save_streamers)
from .ingest import VIDEO_EXTS, is_url
from .live import live_config
from .jobs import LiveManager, Store, Worker, read_clip, read_manifest, remove_job_files, write_clip

WEB_DIR = ROOT / "web"
UPLOAD_DIR = DATA_DIR / "uploads"
JOB_ID_RE = re.compile(r"^[0-9A-Za-z\-]+$")
CLIP_RE = re.compile(r"^clip_\d{2}$")

app = FastAPI(title="GTA VI Clip Pipeline", version=__version__)
store = Store()
worker = Worker(store, concurrency=int(os.environ.get("CLIPPER_CONCURRENCY", "1")))
live_mgr = LiveManager(store, worker)

PASSWORD = os.environ.get("CLIPPER_PASSWORD")


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    """Optional password protection (set CLIPPER_PASSWORD) when exposed to the internet."""
    if PASSWORD and request.url.path != "/api/health":
        header = request.headers.get("authorization", "")
        ok = False
        if header.lower().startswith("basic "):
            try:
                _user, _, pw = base64.b64decode(header[6:]).decode().partition(":")
                ok = secrets.compare_digest(pw, PASSWORD)
            except Exception:
                ok = False
        if not ok:
            return Response("Authentication required", status_code=401,
                            headers={"WWW-Authenticate": 'Basic realm="clipper"'})
    return await call_next(request)


# ------------------------------------------------------------------ models
class JobOptions(BaseModel):
    count: int | None = Field(None, ge=1, le=50)
    min_seconds: float | None = Field(None, ge=5, le=180)
    max_seconds: float | None = Field(None, ge=5, le=180)
    target_seconds: float | None = Field(None, ge=5, le=180)
    layout: str | None = None
    facecam_box: list[float] | None = None
    fps: int | None = Field(None, ge=24, le=60)
    captions: bool | None = None
    whisper_model: str | None = None
    language: str | None = None
    scene_detection: bool | None = None
    credit_overlay: bool | None = None
    streamer: str | None = None
    auto_upload: bool = False
    privacy: str | None = None


class JobRequest(JobOptions):
    url: str
    live: bool = False                                        # record a live stream, clip it every N minutes
    chunk_minutes: int | None = Field(None, ge=5, le=120)    # live only; `count` = clips per chunk


class ClipEdit(BaseModel):
    title: str | None = Field(None, max_length=100)
    description: str | None = Field(None, max_length=4900)
    tags: list[str] | None = None


class Streamer(BaseModel):
    name: str
    match: list[str] = []
    credit_url: str = ""
    permission: str = ""


class StreamerList(BaseModel):
    enforce: bool = True
    streamers: list[Streamer]


def _overrides(o: JobOptions) -> dict:
    if o.layout and o.layout not in LAYOUTS:
        raise HTTPException(400, f"layout must be one of {LAYOUTS}")
    if o.whisper_model and o.whisper_model not in WHISPER_MODELS:
        raise HTTPException(400, f"whisper_model must be one of {WHISPER_MODELS}")
    return {
        "clips": {"count": o.count, "min_seconds": o.min_seconds, "max_seconds": o.max_seconds,
                  "target_seconds": o.target_seconds},
        "render": {"layout": o.layout, "facecam_box": o.facecam_box, "credit_overlay": o.credit_overlay,
                   "fps": o.fps},
        "captions": {"enabled": o.captions, "model": o.whisper_model, "language": o.language or None},
        "detection": {"scene_detection": o.scene_detection},
        "youtube": {"privacy": o.privacy},
    }


def _job_dir(job_id: str) -> Path:
    if not JOB_ID_RE.match(job_id):
        raise HTTPException(400, "bad job id")
    return worker.job_dir(job_id)


# ------------------------------------------------------------------ API
@app.get("/api/health")
def health():
    return {"ok": True, "version": __version__}


@app.get("/api/config")
def config():
    s = load_settings()
    return {
        "layouts": LAYOUTS,
        "whisper_models": WHISPER_MODELS,
        "defaults": {
            "count": s["clips"]["count"], "min_seconds": s["clips"]["min_seconds"],
            "max_seconds": s["clips"]["max_seconds"], "target_seconds": s["clips"]["target_seconds"],
            "layout": s["render"]["layout"], "facecam_box": s["render"]["facecam_box"],
            "fps": s["render"]["fps"],
            "captions": s["captions"]["enabled"], "whisper_model": s["captions"]["model"],
            "scene_detection": s["detection"].get("scene_detection", False),
            "credit_overlay": s["render"].get("credit_overlay", True),
            "privacy": s["youtube"]["privacy"], "auto_upload": s["youtube"].get("auto_upload", False),
            "chunk_minutes": live_config(s)["chunk_minutes"], "clips_per_chunk": live_config(s)["clips_per_chunk"],
        },
        "youtube": {**youtube.status(), "uploads_24h": store.uploads_last_24h(),
                    "daily_cap": s["youtube"].get("daily_upload_cap", 6)},
        "password_protected": bool(PASSWORD),
    }


@app.post("/api/jobs")
def create_job(req: JobRequest):
    url = req.url.strip()
    if not is_url(url):
        raise HTTPException(400, "Paste a full http(s) VOD URL, or use file upload.")
    overrides = _overrides(req)
    if req.live:
        overrides["live"] = {"chunk_minutes": req.chunk_minutes, "clips_per_chunk": req.count}
        overrides["clips"]["count"] = None
        return {"id": live_mgr.start(url, {"overrides": overrides, "auto_upload": req.auto_upload})}
    opts = {"overrides": overrides, "streamer": req.streamer, "auto_upload": req.auto_upload}
    return {"id": worker.submit(url, opts)}


@app.post("/api/jobs/upload")
async def create_job_upload(file: UploadFile = File(...), options: str = Form("{}")):
    try:
        o = JobOptions.model_validate_json(options or "{}")
    except Exception as exc:
        raise HTTPException(400, f"bad options: {exc}")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in VIDEO_EXTS:
        raise HTTPException(400, f"Unsupported file type {ext or '(none)'}; use {sorted(VIDEO_EXTS)}")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = UPLOAD_DIR / f"{uuid.uuid4().hex}{ext}"
    def _save() -> None:
        with dest.open("wb") as fh:
            shutil.copyfileobj(file.file, fh, length=8 * 1024 * 1024)

    await run_in_threadpool(_save)
    opts = {"overrides": _overrides(o), "streamer": o.streamer, "auto_upload": o.auto_upload,
            "delete_upload_after": True, "filename": file.filename}
    return {"id": worker.submit(str(dest), opts)}


@app.get("/api/jobs")
def list_jobs():
    return store.list()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    job["manifest"] = read_manifest(_job_dir(job_id))
    job["uploads"] = store.uploads_for(job_id)
    return job


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if job["status"] == "queued":
        store.update(job_id, status="cancelled", message="Cancelled", cancel=1)
    elif job["status"] == "running":
        store.update(job_id, cancel=1, message="Cancelling…")
    return {"ok": True}


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str):
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if job["status"] not in ("failed", "cancelled"):
        raise HTTPException(400, "only failed/cancelled jobs can be retried")
    if job["options"].get("mode") == "live":
        opts = {k: v for k, v in job["options"].items() if k != "mode"}
        return {"id": live_mgr.start(job["source"], opts)}
    if not is_url(job["source"]) and not Path(job["source"]).exists():
        raise HTTPException(400, "uploaded file is gone; upload it again")
    return {"id": worker.submit(job["source"], job["options"])}


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if job["status"] == "running":
        raise HTTPException(400, "cancel the job first")
    remove_job_files(_job_dir(job_id))
    store.delete(job_id)
    return {"ok": True}


@app.patch("/api/jobs/{job_id}/clips/{clip}")
def edit_clip(job_id: str, clip: str, edit: ClipEdit):
    if not CLIP_RE.match(clip):
        raise HTTPException(400, "bad clip name")
    d = _job_dir(job_id)
    try:
        data = read_clip(d, clip)
    except FileNotFoundError:
        raise HTTPException(404, "clip not found")
    data.update({k: v for k, v in edit.model_dump().items() if v is not None})
    write_clip(d, clip, data)
    return data


@app.post("/api/jobs/{job_id}/clips/{clip}/youtube")
def upload_clip(job_id: str, clip: str):
    if not CLIP_RE.match(clip):
        raise HTTPException(400, "bad clip name")
    if not youtube.status()["authorized"]:
        raise HTTPException(400, "YouTube is not connected. Run `python -m clipper auth-youtube` (see README).")
    try:
        read_clip(_job_dir(job_id), clip)
    except FileNotFoundError:
        raise HTTPException(404, "clip not found")
    return {"upload_id": worker.queue_upload(job_id, clip)}


@app.get("/api/streamers")
def get_streamers():
    return load_streamers()


@app.put("/api/streamers")
def put_streamers(body: StreamerList):
    save_streamers(body.model_dump())
    return load_streamers()


@app.get("/media/{job_id}/{kind}/{name}")
def media(job_id: str, kind: str, name: str):
    if kind != "clips" or not re.match(r"^clip_\d{2}\.(mp4|jpg|ass|json)$", name):
        raise HTTPException(404)
    path = _job_dir(job_id) / kind / name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


@app.exception_handler(Exception)
async def unhandled(_request: Request, exc: Exception):
    return JSONResponse({"detail": str(exc)}, status_code=500)


app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
