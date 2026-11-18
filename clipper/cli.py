"""Command line interface: `python -m clipper <command>`."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path


def _overrides(a: argparse.Namespace) -> dict:
    box = [float(x) for x in a.facecam_box.split(",")] if getattr(a, "facecam_box", None) else None
    return {
        "clips": {"count": a.clips, "min_seconds": a.min_seconds, "max_seconds": a.max_seconds},
        "render": {"layout": a.layout, "facecam_box": box, "fps": a.fps,
                   "credit_overlay": False if a.no_credit else None},
        "captions": {"enabled": False if a.no_captions else None, "model": a.whisper_model,
                     "language": a.language},
        "detection": {"scene_detection": True if a.scenes else None},
        "youtube": {"privacy": a.privacy},
        "download": {"cookies_file": a.cookies},
    }


def cmd_run(a: argparse.Namespace) -> int:
    from . import pipeline

    out = Path(a.out or f"out/{time.strftime('%Y%m%d-%H%M%S')}")

    def progress(stage: str, pct: float, msg: str) -> None:
        if msg:
            print(f"[{pct * 100:5.1f}%] {stage:<10} {msg}", flush=True)

    manifest = pipeline.run(a.source, out, _overrides(a), pipeline.Reporter(on_progress=progress),
                            streamer_override=a.streamer)
    print(f"\n✔ {len(manifest['clips'])} clips in {out}/clips")
    for c in manifest["clips"]:
        print(f"  {c['file']}  {c['duration']:>5.1f}s  score {c['score']:>6.2f}  {c['title']}")
    if a.upload:
        _upload_dir(out, manifest)
    return 0


def _upload_dir(out: Path, manifest: dict) -> None:
    from . import youtube
    from .config import load_settings

    settings = load_settings()
    yt_cfg = {**settings["youtube"], **((manifest.get("settings") or {}).get("youtube") or {})}
    cap = int(settings["youtube"].get("daily_upload_cap", 6))
    for c in manifest["clips"][:cap]:
        # clip json holds any edits made after rendering
        clip_json = out / "clips" / f"{c['name']}.json"
        if clip_json.exists():
            c = json.loads(clip_json.read_text())
        res = youtube.upload(out / c["file"], c["title"], c["description"], c.get("tags", []),
                             yt_cfg, out / c["thumbnail"] if c.get("thumbnail") else None)
        print(f"  ↑ uploaded {c['name']}: {res['url']}")


def cmd_upload(a: argparse.Namespace) -> int:
    out = Path(a.dir)
    manifest = json.loads((out / "manifest.json").read_text())
    _upload_dir(out, manifest)
    return 0


def cmd_serve(a: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("clipper.server:app", host=a.host, port=a.port, log_level="info")
    return 0


def cmd_auth(a: argparse.Namespace) -> int:
    from . import youtube

    path = youtube.authorize(port=a.port, open_browser=not a.no_browser)
    print(f"✔ Saved YouTube token to {path}")
    print("For GitHub Actions: copy that file's contents into the YT_TOKEN_JSON repo secret.")
    return 0


def cmd_watch(a: argparse.Namespace) -> int:
    from .watch import run_watch

    results = run_watch(Path(a.state), Path(a.out), dry_run=a.dry_run)
    print(json.dumps(results, indent=2))
    if a.upload and not a.dry_run:
        for r in results:
            if r.get("outdir"):
                out = Path(r["outdir"])
                _upload_dir(out, json.loads((out / "manifest.json").read_text()))
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "WARNING"), format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="clipper", description="GTA VI stream → Shorts clip pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="process one VOD URL or local video file")
    r.add_argument("source", help="Twitch/YouTube/Kick VOD URL or path to a video file")
    r.add_argument("--out", help="output directory (default out/<timestamp>)")
    r.add_argument("--clips", type=int)
    r.add_argument("--min-seconds", type=float)
    r.add_argument("--max-seconds", type=float)
    r.add_argument("--layout", choices=["blur", "facecam", "crop", "landscape"])
    r.add_argument("--facecam-box", help="x,y,w,h normalized, e.g. 0,0.62,0.28,0.38")
    r.add_argument("--fps", type=int, choices=[30, 60])
    r.add_argument("--no-captions", action="store_true")
    r.add_argument("--no-credit", action="store_true", help="hide the on-video credit line")
    r.add_argument("--whisper-model", choices=["tiny", "base", "small", "medium"])
    r.add_argument("--language")
    r.add_argument("--scenes", action="store_true", help="enable scene-change signal (local files)")
    r.add_argument("--streamer", help="streamer name for credit (local files)")
    r.add_argument("--cookies", help="cookies.txt for yt-dlp")
    r.add_argument("--privacy", choices=["private", "unlisted", "public"])
    r.add_argument("--upload", action="store_true", help="upload clips to YouTube when done")
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("serve", help="start the web app")
    s.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    s.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    s.set_defaults(func=cmd_serve)

    u = sub.add_parser("upload", help="upload an output directory's clips to YouTube")
    u.add_argument("dir")
    u.set_defaults(func=cmd_upload)

    au = sub.add_parser("auth-youtube", help="authorize YouTube uploads (one time)")
    au.add_argument("--port", type=int, default=8090)
    au.add_argument("--no-browser", action="store_true")
    au.set_defaults(func=cmd_auth)

    w = sub.add_parser("watch", help="process new VODs from config/watchlist.yaml")
    w.add_argument("--state", default="state/processed.json")
    w.add_argument("--out", default="out")
    w.add_argument("--dry-run", action="store_true")
    w.add_argument("--upload", action="store_true")
    w.set_defaults(func=cmd_watch)

    a = p.parse_args(argv)
    try:
        return a.func(a)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"✘ {exc}", file=sys.stderr)
        return 1
