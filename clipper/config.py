"""Settings loading and per-job overrides."""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = Path(os.environ.get("CLIPPER_CONFIG_DIR", ROOT / "config"))
DATA_DIR = Path(os.environ.get("CLIPPER_DATA_DIR", ROOT / "data"))

LAYOUTS = ("blur", "facecam", "crop", "landscape")
WHISPER_MODELS = ("tiny", "base", "small", "medium")


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def deep_merge(base: dict, override: dict | None) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        elif value is not None:
            out[key] = copy.deepcopy(value)
    return out


def load_settings(overrides: dict | None = None) -> dict:
    settings = _load_yaml(CONFIG_DIR / "settings.yaml")
    return validate(deep_merge(settings, overrides))


def validate(s: dict) -> dict:
    c = s.setdefault("clips", {})
    c["count"] = max(1, min(int(c.get("count", 5)), 50))
    c["min_seconds"] = max(5.0, float(c.get("min_seconds", 20)))
    c["max_seconds"] = max(c["min_seconds"], float(c.get("max_seconds", 58)))
    c["target_seconds"] = min(max(float(c.get("target_seconds", 35)), c["min_seconds"]), c["max_seconds"])
    c["pre_roll"] = min(max(float(c.get("pre_roll", 0.6)), 0.05), 0.95)
    c["min_gap_seconds"] = max(0.0, float(c.get("min_gap_seconds", 90)))
    r = s.setdefault("render", {})
    r["fps"] = 60 if int(r.get("fps") or 30) >= 60 else 30
    if r.get("layout") not in LAYOUTS:
        r["layout"] = "blur"
    box = r.get("facecam_box") or [0.0, 0.62, 0.28, 0.38]
    box = [min(max(float(v), 0.0), 1.0) for v in box][:4]
    if len(box) != 4 or box[2] <= 0.01 or box[3] <= 0.01:
        box = [0.0, 0.62, 0.28, 0.38]
    r["facecam_box"] = box
    cap = s.setdefault("captions", {})
    if cap.get("model") not in WHISPER_MODELS:
        cap["model"] = "base"
    yt = s.setdefault("youtube", {})
    if yt.get("privacy") not in ("private", "unlisted", "public"):
        yt["privacy"] = "private"
    return s


# ---------------------------------------------------------------- allowlist
def streamers_path() -> Path:
    return CONFIG_DIR / "streamers.yaml"


def load_streamers() -> dict:
    data = _load_yaml(streamers_path())
    data.setdefault("enforce", True)
    data["streamers"] = data.get("streamers") or []
    return data


def save_streamers(data: dict) -> None:
    header = (
        "# Streamer allowlist + credit info (edited from the web UI).\n"
        "# Only add streamers whose rules allow clipping, or who gave you permission.\n\n"
    )
    clean = {
        "enforce": bool(data.get("enforce", True)),
        "streamers": [
            {
                "name": str(s.get("name", "")).strip(),
                "match": [str(m).strip() for m in (s.get("match") or []) if str(m).strip()],
                "credit_url": str(s.get("credit_url") or "").strip(),
                "permission": str(s.get("permission") or "").strip(),
            }
            for s in data.get("streamers", [])
            if str(s.get("name", "")).strip()
        ],
    }
    path = streamers_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + yaml.safe_dump(clean, sort_keys=False, allow_unicode=True), encoding="utf-8")


def match_streamer(info: dict, streamers: dict) -> dict | None:
    """Return the allowlist entry matching a VOD's metadata, if any."""
    haystack = " ".join(
        str(info.get(k) or "").lower()
        for k in ("uploader", "uploader_id", "channel", "channel_id", "channel_url", "uploader_url", "webpage_url")
    )
    tokens = set(haystack.replace("/", " ").replace("@", " ").split())
    for entry in streamers.get("streamers", []):
        candidates = [entry.get("name", "")] + list(entry.get("match") or [])
        for cand in candidates:
            cand = str(cand).lower().strip().lstrip("@")
            if not cand:
                continue
            if "/" in cand or "." in cand:
                if cand in haystack:
                    return entry
            elif cand in tokens:
                return entry
    return None


def load_watchlist() -> dict:
    data = _load_yaml(CONFIG_DIR / "watchlist.yaml")
    data["channels"] = data.get("channels") or []
    return data


def get_path(settings: dict, *keys: str, default: Any = None) -> Any:
    cur: Any = settings
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur
