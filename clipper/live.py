"""Live mode: record a stream while it airs and clip it in rolling chunks.

How it works
- ffmpeg records the live stream (video + audio, stream copy, no re-encode) into short
  MPEG-TS segments. `-copyts` keeps the stream's own clock, so on YouTube each segment's
  start time is its position in the stream (= the timestamp in the VOD published later).
- Every `live.chunk_minutes`, the newest segments are joined into one chunk file, with some
  earlier footage in front for context, and queued as a normal local-file job: the usual
  pipeline finds the best moments, transcribes and renders them.
- Chunks overlap, so each chunk gets a peak window: a moment belongs to exactly one chunk.
- If ffmpeg drops (expired stream URL, network blip) while the stream is still live, the
  URL is re-resolved and recording resumes. When the stream ends or you press Stop, the
  last partial chunk is clipped too.
"""
from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import json
import os
import statistics

from . import ingest, livechat
from .config import load_streamers, match_streamer

log = logging.getLogger(__name__)

TAIL = 30.0       # a peak this close to a chunk's end is left to the next chunk (a clip needs room after it)
MAX_GAP = 3.0     # a hole bigger than this between segments splits chunks (recording resumed later)

DEFAULTS = {
    "chunk_minutes": 20,      # clip the stream every N minutes while it airs
    "clips_per_chunk": 2,
    "segment_seconds": 60,
    "context_seconds": 60,    # earlier footage added in front of each chunk (build-up to a moment)
    "min_final_seconds": 120, # a leftover shorter than this at the end isn't clipped
    "poll_seconds": 10,
    "max_restarts": 20,       # give up after this many recorder restarts in a row without new footage
    "chat": True,             # record the live chat too (YouTube, Twitch): the strongest hype signal
}


class LiveError(RuntimeError):
    pass


@dataclass
class Segment:
    path: Path
    start: float      # seconds into the stream
    duration: float
    wall_end: float = 0.0   # wall-clock time the recorder finished writing it (maps chat to stream time)

    @property
    def end(self) -> float:
        return self.start + self.duration


@dataclass
class ChunkPlan:
    segments: list[Segment]
    offset: float             # stream time of the chunk's first frame
    peak_from: float          # chunk-relative window in which this chunk may pick moments
    peak_to: float | None     # None = up to the chunk's end
    next_cursor: float        # stream time where the next chunk's window starts

    @property
    def end(self) -> float:
        return self.segments[-1].end


def live_config(settings: dict) -> dict:
    return {**DEFAULTS, **(settings.get("live") or {})}


# ------------------------------------------------------------------ planning (pure)
def contiguous_runs(segs: list[Segment]) -> list[list[Segment]]:
    runs: list[list[Segment]] = []
    for s in sorted(segs, key=lambda s: s.start):
        if runs and s.start - runs[-1][-1].end <= MAX_GAP:
            runs[-1].append(s)
        else:
            runs.append([s])
    return runs


def plan_chunk(segs: list[Segment], cursor: float | None, cfg: dict, final: bool) -> ChunkPlan | None:
    """Decide whether to cut the next chunk from the completed segments.

    `cursor` = stream time where the next peak window starts (None = nothing clipped yet).
    A chunk is cut once `chunk_minutes` of new footage (plus TAIL) exist. A run of segments
    that is closed (the stream ended, or recording resumed after a gap) is clipped down to
    its end if the leftover is at least `min_final_seconds`.
    """
    chunk_s = float(cfg["chunk_minutes"]) * 60
    context = float(cfg["context_seconds"])
    min_final = float(cfg["min_final_seconds"])
    runs = contiguous_runs(segs)
    for i, run in enumerate(runs):
        if cursor is not None and run[-1].end <= cursor:
            continue                                  # already covered
        closed = final or i < len(runs) - 1
        win_from = run[0].start if cursor is None or cursor < run[0].start else cursor
        fresh = run[-1].end - win_from
        if fresh < (min_final if closed else chunk_s + TAIL):
            if closed:
                continue                              # leftover too short to be worth clipping
            return None                               # wait for more footage
        body = [s for s in run if s.end > win_from - context]
        cut = next((k for k, s in enumerate(body) if s.end - win_from >= chunk_s + TAIL), len(body) - 1)
        if closed and run[-1].end - body[cut].end < min_final:
            cut = len(body) - 1                       # don't leave a scrap behind; take it all
        body = body[: cut + 1]
        last = closed and body[-1] is run[-1]
        offset = body[0].start
        return ChunkPlan(
            segments=body,
            offset=offset,
            peak_from=max(0.0, win_from - offset),
            peak_to=None if last else body[-1].end - TAIL - offset,
            next_cursor=body[-1].end if last else body[-1].end - TAIL,
        )
    return None


def record_cmd(inputs: list[str], headers: dict, seg_dir: Path, run: int, segment_seconds: float) -> list[str]:
    """ffmpeg command that records the stream into numbered MPEG-TS segments (no re-encode)."""
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-copyts"]
    hdr = "".join(f"{k}: {v}\r\n" for k, v in (headers or {}).items())
    for url in inputs:
        if hdr and url.startswith("http"):
            cmd += ["-headers", hdr]
        cmd += ["-i", url]
    if len(inputs) > 1:   # YouTube: separate video-only + audio-only playlists
        cmd += ["-map", "0:v:0", "-map", "1:a:0"]
    else:                 # Twitch/Kick: one muxed playlist
        cmd += ["-map", "0:v:0", "-map", "0:a:0?"]
    cmd += ["-c", "copy", "-f", "segment", "-segment_time", str(segment_seconds),
            "-segment_format", "mpegts", str(seg_dir / f"r{run:03d}_%05d.ts")]
    return cmd


# ------------------------------------------------------------------ media helpers
def stream_inputs(url: str, settings: dict) -> tuple[list[str], dict]:
    """Resolve the live stream's media playlist URL(s) with yt-dlp (they expire after hours)."""
    import yt_dlp

    max_h = int((settings.get("render") or {}).get("max_height", 1080))
    opts = ingest._ydl_opts(settings, {"format": ingest._video_format(max_h), "skip_download": True})
    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
        except Exception as exc:
            raise LiveError(ingest._friendly_ytdlp_error(exc)) from exc
    fmts = info.get("requested_formats") or [info]
    urls = [f["url"] for f in fmts if f.get("url")]
    if not urls:
        raise LiveError("Could not find the live stream's video URL.")
    return urls, fmts[0].get("http_headers") or info.get("http_headers") or {}


def probe_segment(path: Path) -> tuple[float, float]:
    """(start time on the stream clock, duration) of a recorded segment."""
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=start_time,duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=False)
    try:
        start, dur = (float(x) for x in out.stdout.strip().split(",")[:2])
        return start, dur
    except Exception as exc:
        raise LiveError(f"unreadable segment {path.name}") from exc


def concat_list(segments: list[Segment]) -> str:
    """ffconcat list. After a reconnect the recorder starts a few seconds back, so a segment
    can overlap the previous one: skip the part that was already recorded (`inpoint`)."""
    lines = ["ffconcat version 1.0"]
    prev_end = None
    for s in segments:
        lines.append("file '" + str(s.path).replace("'", r"'\''") + "'")
        if prev_end is not None and prev_end - s.start > 0.5:
            lines.append(f"inpoint {min(prev_end - s.start, s.duration):.3f}")
        prev_end = s.end if prev_end is None else max(prev_end, s.end)
    return "\n".join(lines) + "\n"


def build_chunk(segments: list[Segment], out: Path) -> Path:
    """Join segments into one MP4 starting at t=0 (stream copy: seconds, even for 20 minutes)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    lst = out.with_suffix(".ffconcat")
    lst.write_text(concat_list(segments), encoding="utf-8")
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
           "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", "-movflags", "+faststart", str(out)]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    lst.unlink(missing_ok=True)
    if res.returncode != 0 or not out.exists():
        out.unlink(missing_ok=True)
        raise LiveError(f"could not build chunk: {res.stderr.strip()[-300:]}")
    return out


def fmt(t: float) -> str:
    t = int(max(0, t))
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"


# ------------------------------------------------------------------ session
class LiveSession:
    """Records one live stream and hands finished chunks to `submit(chunk_path, live_opts)`."""

    def __init__(self, url: str, workdir: Path, settings: dict,
                 submit: Callable[[Path, dict], None],
                 log_fn: Callable[[str], None] = lambda m: None,
                 should_stop: Callable[[], bool] = lambda: False,
                 status_fn: Callable[[str], None] = lambda m: None,
                 on_info: Callable[[dict], None] = lambda info: None):
        self.url, self.workdir, self.settings = url, workdir, settings
        self.cfg = live_config(settings)
        self.submit, self.log, self.should_stop, self.status = submit, log_fn, should_stop, status_fn
        self.on_info = on_info
        self.seg_dir = workdir / "segments"
        self.cursor: float | None = None
        self.chunks = 0
        self.clock_shift: float | None = None    # stream clock -> position in the stream
        self._probed: dict[Path, tuple[float, float]] = {}
        self._proc: subprocess.Popen | None = None
        self._errlog: Path = self.seg_dir / "recorder.log"
        self.info: dict = {}
        self.chat: livechat.ChatRecorder | None = None

    def start_chat(self) -> livechat.ChatRecorder | None:   # network hook (tests replace it)
        return livechat.recorder_for(self.info, self.url, self.workdir / "chat", self.settings)

    # network hooks (tests replace these) --------------------------------------
    def probe(self) -> dict:
        return ingest.probe(self.url, self.settings, allow_live=True)

    def resolve(self) -> tuple[list[str], dict]:
        return stream_inputs(self.url, self.settings)

    # -----------------------------------------------------------------------
    def run(self) -> dict:
        self.seg_dir.mkdir(parents=True, exist_ok=True)
        self.info = self.probe()
        if not self.info.get("is_live"):
            raise LiveError("This stream isn't live (anymore). Use a normal job for VODs.")
        allow = load_streamers()
        if match_streamer(self.info, allow) is None and allow.get("enforce", True):
            raise LiveError(f"Channel “{self.info.get('uploader') or self.info.get('channel_url')}” is not in your "
                            "streamer allowlist. Add it under Settings → Streamers (only streamers who allow clipping).")
        self.on_info(self.info)
        self.log(f"🔴 “{self.info.get('title')}” by {self.info.get('uploader')}: clipping every "
                 f"{self.cfg['chunk_minutes']} min, {self.cfg['clips_per_chunk']} clips per chunk")
        if self.cfg.get("chat", True):
            try:
                self.chat = self.start_chat()
                if self.chat:
                    self.chat.start()
                    self.log("Recording the live chat too (used to find hype moments)")
                else:
                    self.log("Live chat isn't supported for this site; using audio signals only")
            except Exception as exc:   # chat is a bonus; never fail the session for it
                self.chat = None
                self.log(f"⚠ Live chat unavailable ({exc}); using audio signals only")
        seg_s = float(self.cfg["segment_seconds"])
        run, restarts, ended = 0, 0, False
        newest_end, last_new = float("-inf"), time.time()
        try:
            while True:
                if self.should_stop():
                    self.log("Stop requested: clipping what was recorded so far…")
                    break
                if self._proc is not None and self._proc.poll() is None and time.time() - last_new > 3 * seg_s + 60:
                    self.log("No new footage for a while; checking the stream…")
                    self._stop_recorder()                      # treated like an exit below
                if self._proc is None or self._proc.poll() is not None:
                    if run:
                        if not self._still_live():
                            self.log("Stream ended.")
                            ended = True
                            break
                        restarts += 1
                        if restarts > int(self.cfg["max_restarts"]):
                            raise LiveError(f"Recording keeps failing: {self._last_error() or 'ffmpeg exited'}")
                        self.log(f"Recorder stopped ({self._last_error()[-160:] or 'exited'}); reconnecting…")
                        self._sleep(min(30, 2 * restarts), wake_on_exit=False)
                        if self.should_stop():
                            continue
                    run += 1
                    inputs, headers = self.resolve()
                    with self._errlog.open("ab") as err:
                        self._proc = subprocess.Popen(record_cmd(inputs, headers, self.seg_dir, run, seg_s),
                                                      stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err)
                    last_new = time.time()
                    if run == 1:
                        self.log("Recording started")
                segs = self._segments(include_open=False)
                if segs and max(s.end for s in segs) > newest_end:
                    newest_end, restarts, last_new = max(s.end for s in segs), 0, time.time()
                self._cut(segs, final=False)
                self._status(segs)
                self._sleep(float(self.cfg["poll_seconds"]))
        finally:
            self._stop_recorder()
            if self.chat:
                self.chat.stop()      # messages already collected are kept for the last chunk
        self._cut(self._segments(include_open=True), final=True)
        self._cleanup(keep_from=None)
        msg = f"{'Stream ended' if ended else 'Stopped'}: {self.chunks} chunk(s) sent for clipping"
        self.log(msg)
        return {"chunks": self.chunks, "ended": ended, "message": msg}

    # -----------------------------------------------------------------------
    def _still_live(self) -> bool:
        try:
            return bool(self.probe().get("is_live"))
        except Exception as exc:  # network blip: assume it's still on; restarts are capped
            self.log(f"Could not check the stream ({exc}); retrying…")
            return True

    def _last_error(self) -> str:
        try:
            return self._errlog.read_text(errors="replace").strip().splitlines()[-1]
        except Exception:
            return ""

    def _sleep(self, seconds: float, wake_on_exit: bool = True) -> None:
        end = time.time() + seconds
        while time.time() < end and not self.should_stop():
            if wake_on_exit and self._proc is not None and self._proc.poll() is not None:
                return
            time.sleep(0.5)

    def _stop_recorder(self) -> None:
        """Stop ffmpeg. The handle is kept, so the loop sees an exited recorder and checks the stream."""
        p = self._proc
        if p is None or p.poll() is not None:
            return
        try:
            p.terminate()   # ffmpeg closes the open segment cleanly on SIGTERM
            p.wait(timeout=15)
        except Exception:
            p.kill()
            p.wait(timeout=5)

    def _segments(self, include_open: bool) -> list[Segment]:
        files = sorted(self.seg_dir.glob("r*_*.ts"))
        if not include_open and self._proc is not None and self._proc.poll() is None and files:
            files = files[:-1]   # names sort by run, then index: the newest one is still being written
        segs = []
        for f in files:
            if f not in self._probed:
                try:
                    self._probed[f] = (*probe_segment(f), os.stat(f).st_mtime)   # closed: mtime = when it ended
                except (LiveError, OSError):
                    continue
            start, dur, wall = self._probed[f]
            if dur > 0.5:
                segs.append(Segment(f, start, dur, wall))
        if segs and self.clock_shift is None:
            self.clock_shift = self._calibrate(segs[-1])
        shift = self.clock_shift or 0.0
        return [Segment(s.path, s.start + shift, s.duration, s.wall_end) for s in segs]

    def _wall_shift(self, segs: list[Segment]) -> float | None:
        """wall-clock time minus stream time, from when segments finished writing.

        The first segment of each recorder run is skipped: ffmpeg catches up on a few
        seconds of backlog there, faster than real time."""
        runs: dict[str, list[Segment]] = {}
        for s in segs:
            runs.setdefault(s.path.name.split("_")[0], []).append(s)
        diffs = [s.wall_end - s.end for run in runs.values() for s in run[1:] if s.wall_end]
        if not diffs:
            diffs = [s.wall_end - s.end for s in segs if s.wall_end]
        return statistics.median(diffs) if diffs else None

    def _chat_file(self, plan: ChunkPlan, all_segs: list[Segment], chunk: Path) -> str | None:
        """Write the chunk's chat message times (seconds into the chunk) next to it."""
        if not self.chat:
            return None
        shift = self._wall_shift(all_segs)
        if shift is None:
            return None
        walls = self.chat.times_between(plan.offset + shift, plan.end + shift)
        out = chunk.with_suffix(".chat.json")
        out.write_text(json.dumps([round(w - shift - plan.offset, 2) for w in walls]))
        self.chat.forget_before(plan.next_cursor - float(self.cfg["context_seconds"]) + shift - 60)
        return str(out)

    def _calibrate(self, newest: Segment) -> float:
        """YouTube's stream clock = time since the stream started. Other hosts use an arbitrary
        clock; there, estimate the stream position from the wall clock (once, on fresh footage)."""
        started = self.info.get("release_timestamp")
        if not started:
            return 0.0
        expected_end = time.time() - float(started) - 20      # ~20 s of live latency
        if abs(newest.end - expected_end) <= 600:
            return 0.0
        return expected_end - newest.end

    def _status(self, segs: list[Segment]) -> None:
        if not segs:
            self.status("🔴 Recording… waiting for the first minute")
            return
        nxt = (self.cursor if self.cursor is not None else segs[0].start) + float(self.cfg["chunk_minutes"]) * 60 + TAIL
        if (self.chat and not getattr(self, "_chat_warned", False)
                and isinstance(self.chat, livechat.YouTubeChat) and not self.chat.alive()):
            self._chat_warned = True   # yt-dlp ends at once when chat is off for this stream
            self.log(f"⚠ Live chat stopped after {self.chat.count:,} messages (chat may be disabled "
                     "for this stream); clipping continues with audio signals")
        chat = f" · chat {self.chat.count:,} msgs" if self.chat else ""
        self.status(f"🔴 Recording · stream at {fmt(segs[-1].end)} · {self.chunks} chunk(s) sent · "
                    f"next chunk after {fmt(nxt)}{chat}")

    def _cut(self, segs: list[Segment], final: bool) -> None:
        while True:
            plan = plan_chunk(segs, self.cursor, self.cfg, final)
            if plan is None:
                return
            self.chunks += 1
            a = plan.offset + plan.peak_from
            b = plan.end if plan.peak_to is None else plan.offset + plan.peak_to
            try:
                path = build_chunk(plan.segments, self.workdir / f"chunk_{self.chunks:03d}.mp4")
            except LiveError as exc:
                self.log(f"⚠ Skipped {fmt(a)}–{fmt(b)}: {exc}")
            else:
                chat_file = self._chat_file(plan, segs, path)
                n_chat = len(json.loads(Path(chat_file).read_text())) if chat_file else 0
                self.log(f"Chunk {self.chunks}: moments from {fmt(a)}–{fmt(b)} sent for clipping"
                         + (f" ({n_chat:,} chat messages)" if chat_file else ""))
                self.submit(path, {
                    "info": {k: v for k, v in self.info.items() if k not in ("is_live", "duration")},
                    "offset": round(plan.offset, 3),
                    "peak_from": round(plan.peak_from, 3),
                    "peak_to": None if plan.peak_to is None else round(plan.peak_to, 3),
                    "label": f"{fmt(a)}–{fmt(b)}",
                    "chat_file": chat_file,
                })
            self.cursor = plan.next_cursor
            self._cleanup(keep_from=self.cursor - float(self.cfg["context_seconds"]))

    def _cleanup(self, keep_from: float | None) -> None:
        """Delete segments no future chunk needs (all of them at the end)."""
        shift = self.clock_shift or 0.0
        for f in sorted(self.seg_dir.glob("r*_*.ts")):
            st = self._probed.get(f)
            if keep_from is None or (st and st[0] + st[1] + shift <= keep_from):
                f.unlink(missing_ok=True)
                self._probed.pop(f, None)
