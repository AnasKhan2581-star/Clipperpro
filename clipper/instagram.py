"""Publish Reels to Instagram with Meta's official Graph API ("Instagram API with Facebook Login").

Why this route: it's the only one Meta documents for uploading a video file from your own
computer ("resumable upload" to rupload.facebook.com). The simpler "Instagram Login" API
only accepts a video at a public web address.

Setup (once, see docs/INSTAGRAM_SETUP.md):
  1. Instagram clip account = professional (Creator or Business), linked to a Facebook Page.
  2. A Meta developer app (type Business); you are its admin, so no App Review is needed
     for your own account.
  3. Graph API Explorer: a user token with instagram_basic, instagram_content_publish,
     pages_show_list, pages_read_engagement, business_management.
  4. Settings -> Instagram in the web app: paste App ID, App secret and that token -> Connect.
     The app swaps it for a long-lived user token and the Page's token (which doesn't
     expire), finds the linked Instagram account and saves it all to data/instagram.json.

Publishing a Reel:
  POST /{ig-user-id}/media  media_type=REELS, upload_type=resumable, caption   -> container id
  POST rupload.facebook.com/ig-api-upload/{v}/{container}  (headers: Authorization: OAuth,
       offset: 0, file_size) with the MP4 bytes
  GET  /{container}?fields=status_code   until FINISHED
  POST /{ig-user-id}/media_publish  creation_id=container                     -> media id
  GET  /{media}?fields=permalink
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Callable

from .config import DATA_DIR

log = logging.getLogger(__name__)

API_VERSION = "v27.0"
GRAPH = "https://graph.facebook.com"
RUPLOAD = "https://rupload.facebook.com/ig-api-upload"
CONFIG = DATA_DIR / "instagram.json"
CAPTION_MAX = 2200
HASHTAGS_MAX = 30
MAX_BYTES = 300 * 1024 * 1024
POLL_SECONDS = 5.0
POLL_TIMEOUT = 600.0


class InstagramError(RuntimeError):
    pass


def _http():
    import requests
    return requests


def _graph(method: str, path: str, token: str | None = None, **params) -> dict:
    url = path if path.startswith("http") else f"{GRAPH}/{API_VERSION}/{path.lstrip('/')}"
    if token:
        params["access_token"] = token
    req = _http()
    try:
        r = req.request(method, url, params=params if method == "GET" else None,
                        data=None if method == "GET" else params, timeout=60)
    except req.RequestException as exc:
        raise InstagramError(f"Can't reach Instagram ({exc.__class__.__name__}). Check your internet.") from exc
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code >= 400 or "error" in body:
        err = body.get("error") or {}
        msg = err.get("error_user_msg") or err.get("message") or f"HTTP {r.status_code}"
        code = err.get("code")
        if code == 190:
            msg += " (the token expired or was revoked: reconnect Instagram in Settings)"
        elif code in (10, 200) or "permission" in msg.lower():
            msg += " (a permission is missing: regenerate the token with all permissions in docs/INSTAGRAM_SETUP.md)"
        raise InstagramError(f"Instagram: {msg}")
    return body


# ------------------------------------------------------------------ config
def _load() -> dict | None:
    if CONFIG.exists():
        try:
            return json.loads(CONFIG.read_text())
        except ValueError:
            return None
    return None


def status() -> dict:
    cfg = _load() or {}
    ok = bool(cfg.get("ig_user_id") and (cfg.get("page_token") or cfg.get("user_token")))
    return {"connected": ok, "username": cfg.get("username"), "page": cfg.get("page_name"),
            "connected_at": cfg.get("connected_at")}


def disconnect() -> None:
    CONFIG.unlink(missing_ok=True)


def connect(app_id: str, app_secret: str, user_token: str) -> dict:
    """Swap a Graph API Explorer token for lasting tokens and find the linked Instagram account."""
    app_id, app_secret, user_token = app_id.strip(), app_secret.strip(), user_token.strip()
    if not (app_id and app_secret and user_token):
        raise InstagramError("App ID, App secret and the access token are all needed.")
    long_lived = _graph("GET", "oauth/access_token", grant_type="fb_exchange_token", client_id=app_id,
                        client_secret=app_secret, fb_exchange_token=user_token).get("access_token")
    if not long_lived:
        raise InstagramError("Meta didn't return a long-lived token. Check the App ID and App secret.")
    pages = _graph("GET", "me/accounts", long_lived,
                   fields="id,name,access_token,instagram_business_account{id,username}").get("data") or []
    linked = [p for p in pages if p.get("instagram_business_account")]
    if not linked:
        names = ", ".join(p.get("name", "?") for p in pages) or "none"
        raise InstagramError("None of your Facebook Pages has an Instagram professional account linked "
                             f"(Pages found: {names}). Link the clip account to a Page first (setup step 2).")
    page = linked[0]
    ig = page["instagram_business_account"]
    cfg = {
        "app_id": app_id, "app_secret": app_secret,
        "user_token": long_lived, "page_token": page.get("access_token"),
        "page_id": page["id"], "page_name": page.get("name"),
        "ig_user_id": ig["id"], "username": ig.get("username"),
        "connected_at": time.time(),
        "other_pages": [p.get("name") for p in linked[1:]],
    }
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2))
    try:
        CONFIG.chmod(0o600)
    except OSError:
        pass
    return status()


def _creds() -> tuple[str, str]:
    cfg = _load() or {}
    token = cfg.get("page_token") or cfg.get("user_token")
    if not (cfg.get("ig_user_id") and token):
        raise InstagramError("Instagram isn't connected. Open Settings -> Instagram (see docs/INSTAGRAM_SETUP.md).")
    return cfg["ig_user_id"], token


# ------------------------------------------------------------------ caption
def caption_for(clip: dict) -> str:
    """Instagram has one caption: use the description (hook, CTA, credit, hashtags).

    Keeps the first 30 hashtags (Instagram's limit) and 2200 characters."""
    text = (clip.get("description") or clip.get("title") or "").strip()
    seen = 0

    def keep(m: re.Match) -> str:
        nonlocal seen
        seen += 1
        return m.group(0) if seen <= HASHTAGS_MAX else ""
    text = re.sub(r"(?<!\w)#\w+", keep, text)
    text = re.sub(r"[ \t]+\n", "\n", re.sub(r"[ \t]{2,}", " ", text)).strip()
    if len(text) > CAPTION_MAX:
        text = text[: CAPTION_MAX - 1].rsplit(" ", 1)[0] + "…"
    return text


# ------------------------------------------------------------------ publish
def upload(video: Path, caption: str, progress: Callable[[str], None] | None = None,
           sleep: Callable[[float], None] = time.sleep) -> dict:
    say = progress or (lambda m: None)
    video = Path(video)
    size = video.stat().st_size
    if size > MAX_BYTES:
        raise InstagramError(f"The video is {size / 1e6:.0f} MB; Instagram allows 300 MB.")
    ig_id, token = _creds()

    say("Creating the Reel on Instagram…")
    created = _graph("POST", f"{ig_id}/media", token, media_type="REELS", upload_type="resumable",
                     caption=caption)
    container = created.get("id")
    if not container:
        raise InstagramError("Instagram didn't create the Reel container.")
    upload_url = created.get("uri") or f"{RUPLOAD}/{API_VERSION}/{container}"

    say(f"Sending the video ({size / 1e6:.1f} MB)…")
    req = _http()
    try:
        with video.open("rb") as fh:
            r = req.post(upload_url, data=fh, timeout=600,
                         headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size)})
    except req.RequestException as exc:
        raise InstagramError(f"Video upload failed ({exc.__class__.__name__}).") from exc
    if r.status_code >= 400:
        raise InstagramError(f"Instagram rejected the video upload (HTTP {r.status_code}): {r.text[:200]}")

    say("Instagram is processing the video…")
    waited, last = 0.0, ""
    while True:
        st = _graph("GET", container, token, fields="status_code,status")
        last = st.get("status_code") or ""
        if last == "FINISHED":
            break
        if last in ("ERROR", "EXPIRED"):
            raise InstagramError(f"Instagram couldn't process the video: {st.get('status') or last}")
        if waited >= POLL_TIMEOUT:
            raise InstagramError("Instagram is still processing after 10 minutes; try again later.")
        sleep(POLL_SECONDS)
        waited += POLL_SECONDS

    say("Publishing…")
    media = _graph("POST", f"{ig_id}/media_publish", token, creation_id=container).get("id")
    if not media:
        raise InstagramError("Instagram didn't return the published Reel.")
    try:
        link = _graph("GET", media, token, fields="permalink").get("permalink")
    except InstagramError:
        link = None
    return {"video_id": media, "url": link or "https://www.instagram.com/"}


def publishing_limit() -> dict | None:
    """Instagram's own 24 h quota for API posts ({"quota_usage", "config": {"quota_total"}})."""
    try:
        ig_id, token = _creds()
        data = _graph("GET", f"{ig_id}/content_publishing_limit", token, fields="quota_usage,config").get("data")
        return data[0] if data else None
    except InstagramError:
        return None
