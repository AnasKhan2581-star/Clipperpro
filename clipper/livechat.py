"""Record a live stream's chat while it airs, so live mode gets the chat signal too.

Chat is the strongest hype signal we have (viewers spam when something happens), but a
live stream has no chat replay yet. These recorders collect the wall-clock time of every
message while the stream runs:

- YouTube: yt-dlp's live-chat downloader (writes JSON lines as messages arrive).
- Twitch:  the public chat over IRC with an anonymous login (no account needed).

Live mode maps wall-clock time to stream time with the recorded video segments
(see `live.LiveSession._wall_shift`), so the same code works for every site.
"""
from __future__ import annotations

import logging
import random
import re
import socket
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

_TS_RE = re.compile(rb'"timestampUsec":\s*"(\d{13,})"')


class ChatRecorder:
    """Base: collects message wall times (seconds since epoch)."""

    def __init__(self) -> None:
        self._times: list[float] = []
        self._total = 0
        self._lock = threading.Lock()

    def start(self) -> None:  # pragma: no cover - overridden
        pass

    def stop(self) -> None:  # pragma: no cover - overridden
        pass

    def poll(self) -> None:
        """Pull in new messages (file-based recorders)."""

    def add(self, t: float) -> None:
        with self._lock:
            self._times.append(t)
            self._total += 1

    def times_between(self, a: float, b: float) -> list[float]:
        self.poll()
        with self._lock:
            return [t for t in self._times if a <= t < b]

    def forget_before(self, t: float) -> None:
        with self._lock:
            self._times = [x for x in self._times if x >= t]

    @property
    def count(self) -> int:
        """Messages received so far (including ones already handed to chunks)."""
        self.poll()
        with self._lock:
            return self._total


class YouTubeChat(ChatRecorder):
    """yt-dlp --write-subs live_chat on a live stream: one JSON line per message, as they arrive."""

    def __init__(self, url: str, workdir: Path, cookies: str | None = None):
        super().__init__()
        self.url, self.dir = url, workdir
        self.cookies = cookies
        self._proc: subprocess.Popen | None = None
        self._pos: dict[Path, int] = {}

    def start(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        cmd = ["yt-dlp", "--no-warnings", "--quiet", "--skip-download", "--write-subs", "--sub-langs", "live_chat",
               "-o", str(self.dir / "chat.%(ext)s"), self.url]
        if self.cookies:
            cmd[1:1] = ["--cookies", self.cookies]
        self._proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL)

    def poll(self) -> None:
        for f in self.dir.glob("chat*.live_chat.json*"):
            pos = self._pos.get(f, 0)
            try:
                with f.open("rb") as fh:
                    fh.seek(pos)
                    data = fh.read()
            except OSError:
                continue
            end = data.rfind(b"\n") + 1          # only complete lines
            if end <= 0:
                continue
            self._pos[f] = pos + end
            stamps = []
            for line in data[:end].split(b"\n"):
                if b"liveChatTextMessageRenderer" in line:   # real chat; not paid/membership/system events
                    m = _TS_RE.search(line)
                    if m:
                        stamps.append(int(m.group(1)) / 1e6)
            with self._lock:
                self._times.extend(stamps)
                self._total += len(stamps)

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def stop(self) -> None:
        self.poll()
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except Exception:
                self._proc.kill()


class TwitchChat(ChatRecorder):
    """Anonymous read-only IRC connection to a Twitch channel's chat."""

    HOST, PORT = "irc.chat.twitch.tv", 6667

    def __init__(self, channel: str):
        super().__init__()
        self.channel = channel.lower().lstrip("#")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"twitch-chat-{self.channel}", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        backoff = 2
        while not self._stop.is_set():
            try:
                with socket.create_connection((self.HOST, self.PORT), timeout=30) as s:
                    s.sendall(f"NICK justinfan{random.randint(10000, 99999)}\r\nJOIN #{self.channel}\r\n".encode())
                    s.settimeout(1.0)
                    buf = b""
                    backoff = 2
                    while not self._stop.is_set():
                        try:
                            data = s.recv(65536)
                        except socket.timeout:
                            continue
                        if not data:
                            break
                        now = time.time()
                        buf += data
                        *lines, buf = buf.split(b"\r\n")
                        for line in lines:
                            if line.startswith(b"PING"):
                                s.sendall(b"PONG" + line[4:] + b"\r\n")
                            elif b" PRIVMSG #" in line:
                                self.add(now)
            except OSError as exc:
                log.info("twitch chat %s: %s; reconnecting", self.channel, exc)
            self._stop.wait(backoff)
            backoff = min(backoff * 2, 60)

    def stop(self) -> None:
        self._stop.set()


def recorder_for(info: dict, url: str, workdir: Path, settings: dict) -> ChatRecorder | None:
    """Pick the chat recorder for a live stream's site (None if unsupported)."""
    extractor = (info.get("extractor") or "").lower()
    if "youtube" in extractor:
        cookies = (settings.get("download") or {}).get("cookies_file")
        return YouTubeChat(url, workdir, cookies if cookies and Path(cookies).exists() else None)
    if "twitch" in extractor:
        m = re.search(r"twitch\.tv/([A-Za-z0-9_]+)", url)
        channel = ((m.group(1) if m else "") or info.get("uploader_id") or "").strip()
        return TwitchChat(channel) if channel else None
    return None
