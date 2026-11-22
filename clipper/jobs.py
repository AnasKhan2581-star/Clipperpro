"""SQLite-backed job store + background worker threads."""
from __future__ import annotations

import json
import logging
import queue
import shutil
import sqlite3
import threading
import time
import traceback
import uuid
from pathlib import Path

from . import live, pipeline, youtube
from .config import DATA_DIR, deep_merge, load_settings

log = logging.getLogger(__name__)

JOBS_DIR = DATA_DIR / "jobs"
DB_PATH = DATA_DIR / "clipper.db"


class Store:
    def __init__(self, path: Path = DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY, source TEXT, title TEXT, status TEXT, stage TEXT,
              progress REAL, message TEXT, error TEXT, options TEXT, logs TEXT,
              created_at REAL, updated_at REAL, cancel INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS uploads (
              id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, clip TEXT, status TEXT,
              video_id TEXT, url TEXT, error TEXT, created_at REAL);
            """
        )
        # a re-cut interrupted by a restart: the job's clips are still fine
        self._db.execute("UPDATE jobs SET status='done', message='Re-cut interrupted by restart; try again' "
                         "WHERE status='running' AND stage='trim'")
        # jobs interrupted by a restart can't resume mid-render; mark them failed
        self._db.execute("UPDATE jobs SET status='failed', error='Interrupted by restart' "
                         "WHERE status IN ('running')")
        self._db.commit()

    def _exec(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return cur

    def create_job(self, source: str, options: dict) -> str:
        job_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        now = time.time()
        self._exec("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0)",
                   (job_id, source, None, "queued", "queued", 0.0, "Waiting in queue", None,
                    json.dumps(options), "[]", now, now))
        return job_id

    def update(self, job_id: str, **fields) -> None:
        if not fields:
            return
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        self._exec(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._row(row) if row else None

    def list(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._row(r, light=True) for r in rows]

    def delete(self, job_id: str) -> None:
        self._exec("DELETE FROM jobs WHERE id=?", (job_id,))
        self._exec("DELETE FROM uploads WHERE job_id=?", (job_id,))

    def _row(self, row: sqlite3.Row, light: bool = False) -> dict:
        d = dict(row)
        d["options"] = json.loads(d.get("options") or "{}")
        d["logs"] = [] if light else json.loads(d.get("logs") or "[]")
        d["cancel"] = bool(d.get("cancel"))
        return d

    # uploads ------------------------------------------------------------
    def add_upload(self, job_id: str, clip: str) -> int:
        cur = self._exec("INSERT INTO uploads (job_id, clip, status, created_at) VALUES (?,?,?,?)",
                         (job_id, clip, "queued", time.time()))
        return int(cur.lastrowid)

    def set_upload(self, upload_id: int, **fields) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        self._exec(f"UPDATE uploads SET {cols} WHERE id=?", (*fields.values(), upload_id))

    def uploads_for(self, job_id: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM uploads WHERE job_id=? ORDER BY id", (job_id,)).fetchall()
        return [dict(r) for r in rows]

    def uploads_last_24h(self) -> int:
        with self._lock:
            row = self._db.execute("SELECT COUNT(*) FROM uploads WHERE status='done' AND created_at>?",
                                   (time.time() - 86400,)).fetchone()
        return int(row[0])


class Worker:
    """Runs pipeline jobs one at a time (video encoding saturates the CPU anyway)."""

    def __init__(self, store: Store, concurrency: int = 1):
        self.store = store
        self.q: queue.Queue[str] = queue.Queue()
        self.uq: queue.Queue[tuple[int, str, str]] = queue.Queue()
        for i in range(max(1, concurrency)):
            threading.Thread(target=self._loop, name=f"job-worker-{i}", daemon=True).start()
        threading.Thread(target=self._upload_loop, name="upload-worker", daemon=True).start()
        for job in reversed(store.list(500)):  # requeue jobs that were waiting before a restart
            if job["status"] == "queued":
                self.q.put(job["id"])

    def submit(self, source: str, options: dict) -> str:
        job_id = self.store.create_job(source, options)
        self.q.put(job_id)
        return job_id

    def job_dir(self, job_id: str) -> Path:
        return JOBS_DIR / job_id

    def submit_trim(self, job_id: str, clip: str, start: float, end: float) -> None:
        """Queue a re-cut of one clip (source-timeline seconds). Runs in the job queue, one at a time."""
        self.store.update(job_id, message=f"{clip}: re-cut queued…")
        self.q.put(("trim", job_id, clip, float(start), float(end)))

    def _loop(self) -> None:
        while True:
            item = self.q.get()
            try:
                if isinstance(item, tuple):
                    self._trim(*item[1:])
                else:
                    self._run(item)
            except Exception:  # never let the worker thread die
                log.exception("worker crash on %s", item)

    def _trim(self, job_id: str, clip: str, start: float, end: float) -> None:
        job = self.store.get(job_id)
        if not job:
            return
        prev_logs = job.get("logs") or []
        logs: list[str] = []

        def on_progress(stage: str, pct: float, msg: str) -> None:
            if msg:
                self.store.update(job_id, message=f"{clip}: {msg}", logs=json.dumps((prev_logs + logs)[-200:]))

        self.store.update(job_id, status="running", stage="trim", cancel=0)
        reporter = pipeline.Reporter(on_progress=on_progress, logs=logs,
                                     is_cancelled=lambda: bool((self.store.get(job_id) or {}).get("cancel")))
        try:
            pipeline.rerender_clip(self.job_dir(job_id), clip, start, end, reporter)
            self.store.update(job_id, status="done", stage="done", progress=1.0, cancel=0,
                              message=f"{clip} re-cut to {pipeline._fmt(start)}–{pipeline._fmt(end)}",
                              logs=json.dumps((prev_logs + logs)[-200:]))
        except Exception as exc:  # the job's other clips are fine: keep it "done", say what failed
            log.error("trim %s/%s failed: %s", job_id, clip, traceback.format_exc())
            msg = "cancelled" if isinstance(exc, pipeline.Cancelled) else str(exc)[:300]
            self.store.update(job_id, status="done", stage="done", progress=1.0, cancel=0,
                              message=f"✘ {clip} re-cut failed: {msg}",
                              logs=json.dumps((prev_logs + logs + [f"ERROR: {msg}"])[-200:]))

    def _run(self, job_id: str) -> None:
        job = self.store.get(job_id)
        if not job or job["status"] != "queued":
            return
        opts = job["options"]
        self.store.update(job_id, status="running", stage="probe", message="Starting…")
        logs: list[str] = []
        last_write = [0.0]

        def on_progress(stage: str, pct: float, msg: str) -> None:
            now = time.time()
            fields = {"stage": stage, "progress": pct}
            if msg:
                fields["message"] = msg
                fields["logs"] = json.dumps(logs[-200:])
            if msg or now - last_write[0] > 1.0:
                last_write[0] = now
                self.store.update(job_id, **fields)

        def cancelled() -> bool:
            j = self.store.get(job_id)
            return bool(j and j["cancel"])

        reporter = pipeline.Reporter(on_progress=on_progress, is_cancelled=cancelled, logs=logs)
        outdir = self.job_dir(job_id)
        try:
            manifest = pipeline.run(job["source"], outdir, opts.get("overrides"), reporter,
                                    streamer_override=opts.get("streamer"), live=opts.get("live"))
            self.store.update(job_id, status="done", stage="done", progress=1.0,
                              title=opts.get("filename") or manifest["info"].get("title"), logs=json.dumps(logs[-200:]),
                              message=f"{len(manifest['clips'])} clips ready")
            if opts.get("auto_upload"):
                for clip in manifest["clips"]:
                    self.queue_upload(job_id, clip["name"])
        except pipeline.NoMoments as exc:  # a quiet stretch of a live stream
            self.store.update(job_id, status="done", stage="done", progress=1.0, title=opts.get("filename"),
                              message=str(exc), logs=json.dumps(logs[-200:]))
        except pipeline.Cancelled:
            self.store.update(job_id, status="cancelled", message="Cancelled", logs=json.dumps(logs[-200:]))
        except Exception as exc:
            if cancelled():  # cancel raised inside a yt-dlp hook arrives wrapped
                self.store.update(job_id, status="cancelled", message="Cancelled", logs=json.dumps(logs[-200:]))
                return
            log.error("job %s failed: %s", job_id, traceback.format_exc())
            self.store.update(job_id, status="failed", error=str(exc)[:2000], message="Failed",
                              logs=json.dumps((logs + [f"ERROR: {exc}"])[-200:]))
        finally:
            src = Path(job["source"])
            if opts.get("delete_upload_after") and src.exists() and DATA_DIR in src.resolve().parents:
                src.unlink(missing_ok=True)
            chat = (opts.get("live") or {}).get("chat_file")
            if chat and DATA_DIR in Path(chat).resolve().parents:
                Path(chat).unlink(missing_ok=True)

    # uploads ------------------------------------------------------------
    def queue_upload(self, job_id: str, clip_name: str) -> int:
        upload_id = self.store.add_upload(job_id, clip_name)
        self.uq.put((upload_id, job_id, clip_name))
        return upload_id

    def _upload_loop(self) -> None:
        while True:
            upload_id, job_id, clip_name = self.uq.get()
            try:
                settings = load_settings()
                cap = int(settings["youtube"].get("daily_upload_cap", 6))
                if self.store.uploads_last_24h() >= cap:
                    raise youtube.YouTubeError(f"Daily upload cap reached ({cap}/24h). Raise youtube.daily_upload_cap if your quota allows.")
                self.store.set_upload(upload_id, status="uploading")
                d = self.job_dir(job_id)
                clip = read_clip(d, clip_name)
                manifest = read_manifest(d) or {}
                # the job's own choices (privacy etc.) win over today's defaults
                yt_cfg = {**settings["youtube"], **((manifest.get("settings") or {}).get("youtube") or {})}
                res = youtube.upload(d / clip["file"], clip["title"], clip["description"], clip.get("tags", []),
                                     yt_cfg, d / clip["thumbnail"] if clip.get("thumbnail") else None)
                self.store.set_upload(upload_id, status="done", video_id=res["video_id"], url=res["url"])
            except Exception as exc:
                self.store.set_upload(upload_id, status="failed", error=str(exc)[:1000])


def _plain_title(info: dict) -> str:
    """Stream title without the live dot + date YouTube adds ("🔴 PLAYING GTA 6 2026-11-19 18:01")."""
    import re
    t = re.sub(r"\s+\d{4}-\d{2}-\d{2} \d{2}:\d{2}$", "", info.get("title") or "")
    return t.lstrip("🔴 ").strip()


class LiveManager:
    """Runs live sessions (one thread each). A session records the stream and queues each
    finished chunk as a normal job on the worker, so clips show up as the stream goes on."""

    def __init__(self, store: Store, worker: Worker):
        self.store, self.worker = store, worker

    def start(self, url: str, options: dict) -> str:
        job_id = self.store.create_job(url, {**options, "mode": "live"})
        # never "queued": the worker must not pick it up (also not after a restart)
        self.store.update(job_id, status="running", stage="live", progress=0.0,
                          message="Connecting to the live stream…")
        threading.Thread(target=self._run, args=(job_id,), name=f"live-{job_id}", daemon=True).start()
        return job_id

    def _run(self, job_id: str) -> None:
        job = self.store.get(job_id)
        if not job:
            return
        opts = job["options"]
        overrides = opts.get("overrides") or {}
        logs: list[str] = []

        def log_fn(msg: str) -> None:
            logs.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
            log.info("live %s: %s", job_id, msg)
            self.store.update(job_id, message=msg, logs=json.dumps(logs[-200:]))

        def status_fn(msg: str) -> None:
            self.store.update(job_id, message=msg)

        def on_info(info: dict) -> None:
            self.store.update(job_id, title=f"🔴 LIVE · {_plain_title(info) or job['source']}")

        def should_stop() -> bool:
            j = self.store.get(job_id)
            return bool(j and j["cancel"])

        try:
            settings = load_settings(overrides)
            per_chunk = int(live.live_config(settings)["clips_per_chunk"])

            def submit(path: Path, live_opts: dict) -> None:
                title = f"🔴 {_plain_title(live_opts['info']) or 'Live'} · {live_opts['label']}"
                self.worker.submit(str(path), {
                    "overrides": deep_merge(overrides, {"clips": {"count": per_chunk}}),
                    "live": live_opts, "live_session": job_id, "filename": title,
                    "auto_upload": opts.get("auto_upload", False),
                    "delete_upload_after": True,   # the chunk file is only needed until it's clipped
                })

            session = live.LiveSession(job["source"], self.worker.job_dir(job_id), settings, submit,
                                       log_fn=log_fn, should_stop=should_stop, status_fn=status_fn, on_info=on_info)
            res = session.run()
            self.store.update(job_id, status="done", stage="done", progress=1.0, message=res["message"],
                              logs=json.dumps(logs[-200:]))
        except Exception as exc:
            log.error("live %s failed: %s", job_id, traceback.format_exc())
            self.store.update(job_id, status="failed", error=str(exc)[:2000], message="Failed",
                              logs=json.dumps((logs + [f"ERROR: {exc}"])[-200:]))


def read_manifest(job_dir: Path) -> dict | None:
    p = job_dir / "manifest.json"
    if not p.exists():
        return None
    m = json.loads(p.read_text())
    # clip json files hold user edits (title/description) — they win over the manifest copy
    m["clips"] = [read_clip(job_dir, c["name"]) or c for c in m.get("clips", [])]
    return m


def read_clip(job_dir: Path, name: str) -> dict:
    p = job_dir / "clips" / f"{name}.json"
    if not p.exists():
        raise FileNotFoundError(name)
    return json.loads(p.read_text())


def write_clip(job_dir: Path, name: str, data: dict) -> None:
    (job_dir / "clips" / f"{name}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False))


def remove_job_files(job_dir: Path) -> None:
    shutil.rmtree(job_dir, ignore_errors=True)
