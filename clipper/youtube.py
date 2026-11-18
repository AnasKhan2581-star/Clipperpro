"""YouTube Data API v3 upload using YOUR OWN Google OAuth client (free quota).

Setup (once):
  1. Google Cloud Console -> new project -> enable "YouTube Data API v3".
  2. OAuth consent screen (External, add yourself as test user).
  3. Credentials -> OAuth client ID -> "Desktop app" -> download JSON
     and save it as data/client_secret.json.
  4. Run:  python -m clipper auth-youtube   (opens a URL, approve, done)
     This writes data/youtube_token.json.

For GitHub Actions put the contents of those two files in the repo secrets
YT_CLIENT_SECRET_JSON and YT_TOKEN_JSON.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from .config import DATA_DIR

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]
CLIENT_SECRET = DATA_DIR / "client_secret.json"
TOKEN = DATA_DIR / "youtube_token.json"


class YouTubeError(RuntimeError):
    pass


def _client_config() -> dict | None:
    raw = os.environ.get("YT_CLIENT_SECRET_JSON")
    if raw:
        return json.loads(raw)
    if CLIENT_SECRET.exists():
        return json.loads(CLIENT_SECRET.read_text())
    return None


def _token_info() -> dict | None:
    raw = os.environ.get("YT_TOKEN_JSON")
    if raw:
        return json.loads(raw)
    if TOKEN.exists():
        return json.loads(TOKEN.read_text())
    return None


def status() -> dict:
    return {
        "client_secret": _client_config() is not None,
        "authorized": _token_info() is not None,
    }


def authorize(port: int = 8090, open_browser: bool = False) -> Path:
    from google_auth_oauthlib.flow import InstalledAppFlow

    cfg = _client_config()
    if not cfg:
        raise YouTubeError(f"Missing OAuth client file. Save it as {CLIENT_SECRET}")
    flow = InstalledAppFlow.from_client_config(cfg, SCOPES)
    creds = flow.run_local_server(host="localhost", bind_addr="0.0.0.0", port=port,
                                  open_browser=open_browser, prompt="consent",
                                  authorization_prompt_message="Open this URL in your browser to authorize:\n{url}\n")
    TOKEN.parent.mkdir(parents=True, exist_ok=True)
    TOKEN.write_text(creds.to_json())
    return TOKEN


def _credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    info = _token_info()
    if not info:
        raise YouTubeError("YouTube not authorized. Run `python -m clipper auth-youtube` first.")
    creds = Credentials.from_authorized_user_info(info, SCOPES)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        if not os.environ.get("YT_TOKEN_JSON"):
            TOKEN.write_text(creds.to_json())
    return creds


def upload(video: Path, title: str, description: str, tags: list[str], yt_cfg: dict,
           thumbnail: Path | None = None) -> dict:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    service = build("youtube", "v3", credentials=_credentials(), cache_discovery=False)
    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:4900],
            "tags": [t for t in tags if t][:30],
            "categoryId": str(yt_cfg.get("category_id", "20")),
        },
        "status": {
            "privacyStatus": yt_cfg.get("privacy", "private"),
            "selfDeclaredMadeForKids": bool(yt_cfg.get("made_for_kids", False)),
        },
    }
    media = MediaFileUpload(str(video), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    try:
        request = service.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            _status, response = request.next_chunk()
    except HttpError as exc:
        reason = exc.reason if hasattr(exc, "reason") else str(exc)
        if "quota" in str(exc).lower():
            raise YouTubeError("YouTube API daily quota exceeded. Try again tomorrow.") from exc
        raise YouTubeError(f"YouTube upload failed: {reason}") from exc
    video_id = response["id"]
    if thumbnail and thumbnail.exists():
        try:  # custom thumbnails need a verified channel; ignore if not allowed
            service.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(thumbnail))).execute()
        except Exception as exc:
            log.info("thumbnail not set: %s", exc)
    return {"video_id": video_id, "url": f"https://youtube.com/shorts/{video_id}"}
