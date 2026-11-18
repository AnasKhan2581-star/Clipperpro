"""Watch mode: find new GTA VI VODs from watch-listed channels and process them."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from . import pipeline
from .config import load_settings, load_streamers, load_watchlist, match_streamer
from .ingest import _ydl_opts

log = logging.getLogger(__name__)


def latest_videos(channel_url: str, lookback: int, settings: dict) -> list[dict]:
    import yt_dlp

    opts = _ydl_opts(settings, {"extract_flat": "in_playlist", "playlistend": lookback, "skip_download": True})
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(channel_url, download=False)
    entries = [e for e in (info.get("entries") or []) if e]
    # youtube channel pages can nest tabs; flatten one level
    flat: list[dict] = []
    for e in entries:
        if e.get("_type") == "playlist" and e.get("entries"):
            flat.extend(x for x in e["entries"] if x)
        else:
            flat.append(e)
    out = []
    for e in flat[:lookback]:
        url = e.get("url") or e.get("webpage_url")
        if url and not url.startswith("http"):
            url = f"https://www.youtube.com/watch?v={url}"
        out.append({"id": e.get("id"), "title": e.get("title") or "", "url": url,
                    "live_status": e.get("live_status")})
    return out


def run_watch(state_path: Path, out_root: Path, dry_run: bool = False) -> list[dict]:
    wl = load_watchlist()
    allow = load_streamers()
    settings = load_settings()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state = json.loads(state_path.read_text()) if state_path.exists() else {"processed": []}
    done = set(state.get("processed", []))
    keywords = [k.lower() for k in wl.get("title_keywords") or []]
    budget = int(wl.get("max_new_vods_per_run", 2))
    results = []
    for ch in wl["channels"]:
        if budget <= 0:
            break
        url = ch.get("url")
        if not url:
            continue
        if allow.get("enforce", True) and not match_streamer({"channel_url": url, "webpage_url": url}, allow):
            log.warning("skipping %s: not in streamers.yaml allowlist", url)
            continue
        try:
            vids = latest_videos(url, int(wl.get("lookback", 5)), settings)
        except Exception as exc:
            log.warning("could not list %s: %s", url, exc)
            continue
        for v in vids:
            if budget <= 0:
                break
            key = v["id"] or v["url"]
            if key in done or v.get("live_status") == "is_live":
                continue
            if keywords and not any(k in v["title"].lower() for k in keywords):
                continue
            log.info("new VOD: %s — %s", v["title"], v["url"])
            if dry_run:
                results.append({"vod": v, "dry_run": True})
                budget -= 1
                continue
            overrides = {
                "clips": {"count": ch.get("clips")},
                "render": {"layout": ch.get("layout"), "facecam_box": ch.get("facecam_box")},
            }
            outdir = out_root / f"{(v['id'] or 'vod')}"
            try:
                manifest = pipeline.run(v["url"], outdir, overrides)
                results.append({"vod": v, "outdir": str(outdir), "clips": len(manifest["clips"])})
            except Exception as exc:
                log.error("failed %s: %s", v["url"], exc)
                results.append({"vod": v, "error": str(exc)})
            done.add(key)
            budget -= 1
    state["processed"] = sorted(done)[-1000:]
    if not dry_run:
        state_path.write_text(json.dumps(state, indent=2))
    return results
