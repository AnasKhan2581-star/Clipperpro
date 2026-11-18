"""Titles, descriptions and tags for each clip (with mandatory source credit)."""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


class _Safe(dict):
    def __missing__(self, key: str) -> str:
        return ""


def timestamped_url(url: str | None, seconds: float) -> str:
    if not url:
        return ""
    s = int(max(0, seconds))
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query))
    if "twitch.tv" in parsed.netloc:
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        query["t"] = f"{h}h{m}m{sec}s"
    else:
        query["t"] = f"{s}s"
    return urlunparse(parsed._replace(query=urlencode(query)))


def credit_label(streamer: str, channel_url: str | None) -> str:
    if channel_url:
        short = re.sub(r"^https?://(www\.)?", "", channel_url).rstrip("/")
        return short
    return f"@{streamer}" if streamer else ""


def build(clip: dict, info: dict, streamer: dict, meta_cfg: dict) -> dict:
    name = streamer.get("name") or info.get("uploader") or "Streamer"
    channel_url = streamer.get("credit_url") or info.get("channel_url") or info.get("uploader_url") or ""
    hook = (clip.get("hook") or "").strip().rstrip(".")
    if not hook:
        hook = "this GTA 6 moment"
    hook_title = hook[:1].upper() + hook[1:]
    values = _Safe(
        streamer=name,
        streamer_tag=re.sub(r"[^A-Za-z0-9]", "", name) or "GTA6",
        hook=hook_title,
        source_url=info.get("webpage_url") or "",
        source_url_t=timestamped_url(info.get("webpage_url"), clip.get("start", 0)),
        channel_url=channel_url,
        vod_title=info.get("title") or "",
    )
    title = (meta_cfg.get("title_template") or "{streamer} {hook}").format_map(values)
    title = re.sub(r"\s+", " ", title).strip()
    if len(title) > 100:  # YouTube hard limit
        title = title[:97].rsplit(" ", 1)[0] + "..."
    desc = (meta_cfg.get("description_template") or "{hook}\n\nCredit: {streamer} {channel_url}").format_map(values)
    desc = _drop_empty_fields(desc)
    tags = list(dict.fromkeys([*(meta_cfg.get("tags") or []), name]))
    return {"title": title, "description": desc.strip()[:4900], "tags": tags}


def _drop_empty_fields(text: str) -> str:
    """Remove template lines whose value came out empty (e.g. no source URL for local files)."""
    lines = []
    for line in text.splitlines():
        stripped = line.rstrip()
        if stripped.endswith(":") and len(stripped) < 40:  # "🎥 Full stream:" with nothing after
            continue
        lines.append(re.sub(r"\s+[—-]\s*$", "", stripped))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
