"""Fetching VOD metadata, audio, chat replays and video sections with yt-dlp."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".flv", ".ts", ".m4v"}


class IngestError(RuntimeError):
    pass


def is_url(source: str) -> bool:
    return source.startswith("http://") or source.startswith("https://")


def _ydl_opts(settings: dict, extra: dict | None = None) -> dict:
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 5,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 4,
    }
    cookies = (settings.get("download") or {}).get("cookies_file") or os.environ.get("YTDLP_COOKIES_FILE")
    if cookies and Path(cookies).exists():
        opts["cookiefile"] = str(cookies)
    opts.update(extra or {})
    return opts


def probe(source: str, settings: dict) -> dict:
    """Return normalized metadata for a URL or local file."""
    if not is_url(source):
        path = Path(source)
        if not path.exists():
            raise IngestError(f"File not found: {source}")
        duration = ffprobe_duration(path)
        return {
            "id": path.stem,
            "title": path.stem,
            "uploader": None,
            "channel_url": None,
            "webpage_url": None,
            "duration": duration,
            "extractor": "local",
            "is_local": True,
            "path": str(path),
        }
    import yt_dlp

    with yt_dlp.YoutubeDL(_ydl_opts(settings, {"skip_download": True})) as ydl:
        try:
            info = ydl.extract_info(source, download=False)
        except Exception as exc:  # yt-dlp raises many types
            raise IngestError(_friendly_ytdlp_error(exc)) from exc
    if info.get("_type") == "playlist":
        raise IngestError("That URL is a playlist/channel. Paste a single VOD/video URL (or use the watch list).")
    if info.get("is_live"):
        raise IngestError("Stream is still live. Run the pipeline once the VOD is available.")
    return {
        "id": info.get("id"),
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"),
        "uploader_id": info.get("uploader_id"),
        "channel": info.get("channel"),
        "channel_id": info.get("channel_id"),
        "channel_url": info.get("channel_url") or info.get("uploader_url"),
        "uploader_url": info.get("uploader_url"),
        "webpage_url": info.get("webpage_url") or source,
        "duration": float(info.get("duration") or 0),
        "extractor": (info.get("extractor_key") or info.get("extractor") or "").lower(),
        "is_local": False,
        "has_live_chat": bool((info.get("subtitles") or {}).get("live_chat")),
        "thumbnail": info.get("thumbnail"),
    }


def _friendly_ytdlp_error(exc: Exception) -> str:
    msg = str(exc)
    if "Sign in to confirm" in msg or "not a bot" in msg:
        return ("YouTube blocked the download (bot check). Export cookies.txt from a logged-in browser and set "
                "download.cookies_file (or the YTDLP_COOKIES secret in GitHub Actions), or use a Twitch/Kick VOD.")
    if "Unsupported URL" in msg:
        return "Unsupported URL. Paste a direct VOD link from Twitch, YouTube or Kick."
    first = msg.splitlines()[0] if msg else repr(exc)
    first = re.sub(r"^ERROR:\s*", "", first)
    first = re.sub(r";?\s*please report this issue.*$", "", first, flags=re.I)
    if "Tunnel connection failed" in first or "Unable to connect to proxy" in first:
        return "Network blocked: this machine can't reach the video site (proxy/firewall). " + first[:160]
    return f"Could not read the video: {first[:300]}"


def ffprobe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=False,
    )
    try:
        return float(json.loads(out.stdout)["format"]["duration"])
    except Exception as exc:
        raise IngestError(f"Could not read media file: {path.name}") from exc


def ffprobe_video(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate", "-of", "json", str(path)],
        capture_output=True, text=True, check=False,
    )
    try:
        st = json.loads(out.stdout)["streams"][0]
        num, den = (st.get("r_frame_rate") or "30/1").split("/")
        fps = float(num) / float(den or 1)
        return {"width": int(st["width"]), "height": int(st["height"]), "fps": fps}
    except Exception:
        return {"width": 1920, "height": 1080, "fps": 30.0}


def download_audio(source: str, workdir: Path, settings: dict, progress: Callable[[float], None] | None = None) -> Path:
    """Download the lowest-bitrate audio track (small, fast) for analysis."""
    import yt_dlp

    workdir.mkdir(parents=True, exist_ok=True)

    def hook(d: dict) -> None:
        if progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                progress(min(d.get("downloaded_bytes", 0) / total, 1.0))

    opts = _ydl_opts(settings, {
        # worst audio is plenty for loudness + speech detection
        "format": "worstaudio[acodec!=none]/bestaudio/worst[acodec!=none]/worst",
        "outtmpl": str(workdir / "audio.%(ext)s"),
        "progress_hooks": [hook],
    })
    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            ydl.download([source])
        except Exception as exc:
            raise IngestError(_friendly_ytdlp_error(exc)) from exc
    files = sorted(p for p in workdir.glob("audio.*") if not p.name.endswith(".part"))
    if not files:
        raise IngestError("Audio download produced no file.")
    return files[0]


def download_section(source: str, start: float, end: float, out_path: Path, settings: dict) -> Path:
    """Download only [start, end] of the VOD in up to `render.max_height` quality."""
    import yt_dlp
    from yt_dlp.utils import download_range_func

    out_path.parent.mkdir(parents=True, exist_ok=True)
    max_h = int((settings.get("render") or {}).get("max_height", 1080))
    stem = out_path.with_suffix("")
    opts = _ydl_opts(settings, {
        "format": f"bv*[height<={max_h}]+ba/b[height<={max_h}]/bv*+ba/b",
        "merge_output_format": "mp4",
        "outtmpl": str(stem) + ".%(ext)s",
        "download_ranges": download_range_func(None, [(max(0.0, start), end)]),
        "force_keyframes_at_cuts": True,  # frame-accurate cut so captions line up
    })
    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            ydl.download([source])
        except Exception as exc:
            raise IngestError(_friendly_ytdlp_error(exc)) from exc
    produced = [p for p in stem.parent.glob(stem.name + ".*") if p.suffix.lower() in VIDEO_EXTS]
    if not produced:
        raise IngestError("Section download produced no video file.")
    if produced[0] != out_path:
        shutil.move(str(produced[0]), out_path)
    return out_path


def download_full(source: str, workdir: Path, settings: dict) -> Path:
    import yt_dlp

    max_h = int((settings.get("render") or {}).get("max_height", 1080))
    opts = _ydl_opts(settings, {
        "format": f"bv*[height<={max_h}]+ba/b[height<={max_h}]/bv*+ba/b",
        "merge_output_format": "mp4",
        "outtmpl": str(workdir / "source.%(ext)s"),
    })
    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            ydl.download([source])
        except Exception as exc:
            raise IngestError(_friendly_ytdlp_error(exc)) from exc
    files = [p for p in workdir.glob("source.*") if p.suffix.lower() in VIDEO_EXTS]
    if not files:
        raise IngestError("Download produced no video file.")
    return files[0]


# -------------------------------------------------------------------- chat
def fetch_chat_times(source: str, info: dict, workdir: Path, settings: dict) -> list[float]:
    """Return message timestamps (seconds from VOD start). Empty list if unavailable."""
    try:
        if info.get("has_live_chat"):
            return _youtube_live_chat(source, workdir, settings)
        if "twitch" in (info.get("extractor") or ""):
            return _twitch_chat(source)
    except Exception as exc:  # chat is a bonus signal; never fail the job for it
        log.warning("chat replay unavailable: %s", exc)
    return []


def _youtube_live_chat(source: str, workdir: Path, settings: dict) -> list[float]:
    import yt_dlp

    opts = _ydl_opts(settings, {
        "skip_download": True,
        "writesubtitles": True,
        "subtitleslangs": ["live_chat"],
        "outtmpl": str(workdir / "chat.%(ext)s"),
    })
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([source])
    files = list(workdir.glob("chat*.live_chat.json"))
    times: list[float] = []
    if not files:
        return times
    with files[0].open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                obj = json.loads(line)
                action = obj["replayChatItemAction"]
                offset = float(action.get("videoOffsetTimeMsec", 0)) / 1000.0
                times.append(offset)
            except Exception:
                continue
    return times


def _twitch_chat(source: str) -> list[float]:
    """Twitch chat replay via the optional `chat-downloader` package."""
    try:
        from chat_downloader import ChatDownloader  # type: ignore
    except ImportError:
        log.info("chat-downloader not installed; skipping Twitch chat signal")
        return []
    times = []
    for msg in ChatDownloader().get_chat(source, message_types=["text_message"]):
        t = msg.get("time_in_seconds")
        if t is not None:
            times.append(float(t))
    return times
