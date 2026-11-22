"""End-to-end pipeline: VOD -> hype detection -> transcript -> rendered Shorts."""
from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from . import analysis, captions, highlights, ingest, metadata, render
from .config import deep_merge, load_settings, load_streamers, match_streamer

log = logging.getLogger(__name__)


class PipelineError(RuntimeError):
    pass


class Cancelled(RuntimeError):
    pass


class NoMoments(PipelineError):
    """A live chunk had nothing worth clipping."""


@dataclass
class Reporter:
    """Progress sink. The web worker persists these; the CLI prints them."""
    on_progress: Callable[[str, float, str], None] = lambda stage, pct, msg: None
    is_cancelled: Callable[[], bool] = lambda: False
    logs: list[str] = field(default_factory=list)

    def __call__(self, stage: str, pct: float, msg: str = "") -> None:
        if self.is_cancelled():
            raise Cancelled("Job cancelled")
        if msg:
            line = f"[{time.strftime('%H:%M:%S')}] {msg}"
            self.logs.append(line)
            log.info(msg)
        self.on_progress(stage, round(min(max(pct, 0.0), 1.0), 4), msg)


def run(source: str, outdir: Path, overrides: dict | None = None, reporter: Reporter | None = None,
        streamer_override: str | None = None, live: dict | None = None) -> dict:
    """Run the full pipeline. Returns the manifest dict (also written to outdir/manifest.json).

    `live` is set for a chunk recorded by live mode (see live.py): `info` = the live stream's
    metadata (credit, allowlist, source link), `offset` = the chunk's start in the stream, and
    `peak_from`/`peak_to` = the part of the chunk this chunk may pick moments from
    (chunks overlap, so each moment belongs to exactly one chunk)."""
    rep = reporter or Reporter()
    live = live or {}
    stream_offset = float(live.get("offset") or 0.0)
    settings = load_settings(overrides)
    clip_cfg, det = settings["clips"], settings["detection"]
    outdir.mkdir(parents=True, exist_ok=True)
    work = outdir / "work"
    work.mkdir(exist_ok=True)

    # 1. probe + allowlist ----------------------------------------------------
    rep("probe", 0.01, "Reading video info…")
    info = ingest.probe(source, settings)
    if live.get("info"):  # the chunk is a local file, but credit/links come from the live stream
        info.update({k: v for k, v in live["info"].items() if k not in ("is_local", "path", "duration")})
    if streamer_override:
        info["uploader"] = streamer_override
    duration = float(info.get("duration") or 0)
    if duration <= 0:
        raise PipelineError("Could not determine the video duration.")
    rep("probe", 0.02, f"“{info.get('title')}” by {info.get('uploader') or 'unknown'} — {duration / 60:.1f} min")

    allow = load_streamers()
    entry = match_streamer(info, allow)
    if entry is None and streamer_override:
        entry = match_streamer({"uploader": streamer_override}, allow)
    if entry is None:
        if allow.get("enforce", True) and not info.get("is_local"):
            raise PipelineError(
                f"Channel “{info.get('uploader') or info.get('channel_url')}” is not in your streamer allowlist. "
                "Add it under Settings → Streamers (only streamers who allow clipping) and retry.")
        entry = {"name": streamer_override or info.get("uploader") or "Streamer",
                 "credit_url": info.get("channel_url") or ""}
    rep("probe", 0.03, f"Credit: {entry.get('name')} {entry.get('credit_url') or ''}".strip())
    preset = entry.get("preset") or {}
    if preset:
        # streamer preset (campaign rules) < job options chosen for this run
        preset_cfg = {k: v for k, v in preset.items() if k in settings}
        settings = load_settings(deep_merge(preset_cfg, overrides or {}))
        clip_cfg, det = settings["clips"], settings["detection"]
        rep("probe", 0.03, f"Applied {entry.get('name')} preset")

    # 2. media for analysis ---------------------------------------------------
    local_video: Path | None = Path(source) if info.get("is_local") else None
    if local_video is None and not settings["download"].get("sections", True):
        rep("download", 0.04, "Downloading full VOD (sections mode is off)…")
        local_video = ingest.download_full(source, work, settings)
    if local_video is not None:
        audio_src = local_video
    else:
        rep("download", 0.04, "Downloading audio track for analysis…")
        audio_src = ingest.download_audio(
            source, work, settings, progress=lambda p: rep("download", 0.04 + 0.14 * p))
    rep("download", 0.18, "Audio ready")

    # 3. signals ----------------------------------------------------------------
    rep("analyze", 0.19, "Measuring loudness (yelling, explosions, hype)…")
    db = analysis.loudness_per_second(audio_src, duration, progress=lambda p: rep("analyze", 0.19 + 0.16 * p))
    loud_z, rise_z = analysis.loudness_signals(db, det.get("baseline_seconds", 300))
    signals = {"loudness": loud_z, "loudness_rise": rise_z}

    if live.get("chat_file") and Path(live["chat_file"]).exists():   # chat recorded while the stream was live
        chat_times = json.loads(Path(live["chat_file"]).read_text())
        if chat_times:
            signals["chat"] = analysis.chat_signal(chat_times, len(db))
        rep("analyze", 0.38, f"Live chat: {len(chat_times):,} messages")
    elif not info.get("is_local"):
        rep("analyze", 0.36, "Fetching chat replay (if available)…")
        chat_times = ingest.fetch_chat_times(source, info, work, settings)
        if chat_times:
            signals["chat"] = analysis.chat_signal(chat_times, len(db))
            rep("analyze", 0.38, f"Chat replay: {len(chat_times):,} messages")
        else:
            rep("analyze", 0.38, "No chat replay — using audio signals")
    if det.get("scene_detection") and local_video is not None:
        rep("analyze", 0.39, "Detecting scene changes…")
        signals["scene"] = analysis.scene_signal(local_video, len(db))

    score = highlights.combine(signals, det.get("weights", {}), det.get("smoothing_seconds", 6))
    n_cand = clip_cfg["count"] * max(1, int(det.get("candidate_multiplier", 3)))
    cands = highlights.pick_candidates(score, n_cand, clip_cfg, duration)
    peak_from, peak_to = live.get("peak_from"), live.get("peak_to")
    if peak_from is not None or peak_to is not None:
        lo, hi = float(peak_from or 0.0), float(duration if peak_to is None else peak_to)
        cands = [c for c in cands if lo <= c.peak < hi]
    if not cands:
        if live:  # a quiet stretch of a live stream is normal, not an error
            raise NoMoments("No standout moments in this part of the stream.")
        raise PipelineError("No highlight candidates found (is the video long enough?).")
    rep("analyze", 0.42, f"Found {len(cands)} candidate moments")

    # 4. transcript-based rerank + boundary snapping -------------------------
    cap_cfg = settings["captions"]
    keywords = det.get("keywords") or []
    want_transcript = cap_cfg.get("enabled", True) or det.get("weights", {}).get("keywords", 0) > 0
    warnings: list[str] = []
    seg_cache: dict[int, list[dict]] = {}
    for i, c in enumerate(cands):
        segments: list[dict] = []
        if want_transcript:
            rep("transcribe", 0.42 + 0.28 * i / len(cands), f"Transcribing candidate {i + 1}/{len(cands)}…")
            try:
                segments = _transcribe(audio_src, c.start - 8, c.end + 10, cap_cfg)
            except Cancelled:
                raise
            except Exception as exc:  # captions are optional; keep going
                msg = f"AI captions unavailable ({exc}). Clips were made without captions/keyword ranking."
                rep("transcribe", 0.42 + 0.28 * i / len(cands), "⚠ " + msg)
                warnings.append(msg)
                want_transcript = False
        text = " ".join(s["text"] for s in segments)
        c.keyword_hits = highlights.count_keywords(text, keywords)
        highlights.refine_with_transcript(c, segments, clip_cfg, duration)
        c.hook = highlights.pick_hook(c, segments, keywords)
        seg_cache[id(c)] = segments
    chosen = highlights.finalize(cands, clip_cfg["count"], det.get("weights", {}).get("keywords", 0.8))
    chosen.sort(key=lambda c: c.start)
    rep("transcribe", 0.70, f"Selected {len(chosen)} best clips")

    # 5. download sections + render -------------------------------------------
    rcfg = settings["render"]
    clips_dir = outdir / "clips"
    clips_dir.mkdir(exist_ok=True)
    width, height = render.output_size(rcfg["layout"])
    results = []
    for i, c in enumerate(chosen, start=1):
        base = 0.70 + 0.28 * (i - 1) / len(chosen)
        name = f"clip_{i:02d}"
        if local_video is None:
            rep("render", base, f"Downloading section {i}/{len(chosen)} ({_fmt(c.start)}–{_fmt(c.end)})…")
            try:
                src = ingest.download_section(source, c.start, c.end, work / f"{name}_src.mp4", settings)
                offset = 0.0
            except ingest.IngestError as exc:
                # some hosts don't support seeking; fall back to one full download, cut locally
                rep("render", base, f"Section download failed ({exc}); downloading the full video once instead…")
                local_video = ingest.download_full(source, work, settings)
        if local_video is not None:
            src, offset = local_video, c.start
        rep("render", base + 0.1 / len(chosen), f"Rendering {name} ({rcfg['layout']}, {c.duration:.0f}s)…")
        thumb = _burn(src, offset, c.start, c.end, c.words, clips_dir, name, settings, entry,
                      thumb_at=min(c.peak - c.start, c.duration - 0.5))
        meta = metadata.build({"hook": c.hook, "start": c.start + stream_offset}, info, entry, settings["metadata"])
        clip = {
            "name": name,
            "file": f"clips/{name}.mp4",
            "thumbnail": f"clips/{name}.jpg" if thumb else None,
            "start": c.start, "end": c.end, "duration": round(c.duration, 2), "peak": c.peak,
            "score": round(c.final_score, 3), "keyword_hits": c.keyword_hits, "hook": c.hook,
            "transcript": " ".join(w["word"] for w in c.words),
            "source_link": metadata.timestamped_url(info.get("webpage_url"), c.start + stream_offset),
            **({"stream_start": round(c.start + stream_offset, 2)} if live else {}),
            **meta,
        }
        (clips_dir / f"{name}.json").write_text(json.dumps(clip, indent=2, ensure_ascii=False))
        results.append(clip)
        if src is not local_video:
            src.unlink(missing_ok=True)

    manifest = {
        "source": source,
        "info": {k: v for k, v in info.items() if k != "path"},
        "streamer": entry,
        "settings": settings,
        "stream_offset": stream_offset,      # clip times + this = time in the source stream/VOD
        "is_live_chunk": bool(live),
        "clips": results,
        "warnings": warnings,
        "chart": {
            "duration": duration,
            "score": analysis.resample_for_chart(score),
            "candidates": [c.to_dict() for c in cands],
        },
        "created_at": time.time(),
    }
    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=_json_default))
    shutil.rmtree(work, ignore_errors=True)
    rep("done", 1.0, f"Done — {len(results)} clips ready" + (f" ({len(warnings)} warning)" if warnings else ""))
    return manifest


def _burn(src: Path, offset: float, start: float, end: float, words: list[dict], clips_dir: Path, name: str,
          settings: dict, entry: dict, thumb_at: float) -> Path | None:
    """Captions + credit + CTA, render `src[offset : offset + (end - start)]`, thumbnail.

    `words`, `start` and `end` share one timeline (the one the words were transcribed on)."""
    rcfg, cap_cfg = settings["render"], settings["captions"]
    preset = entry.get("preset") or {}
    width, height = render.output_size(rcfg["layout"])
    credit = None
    if rcfg.get("credit_overlay", True):
        credit = preset.get("credit_label") or metadata.credit_label(entry.get("name", ""), entry.get("credit_url"))
    cta = preset.get("cta")
    ass_path = None
    if (cap_cfg.get("enabled", True) and words) or credit or cta:
        ass = captions.build_ass(words if cap_cfg.get("enabled", True) else [], start, end, cap_cfg,
                                 width=width, height=height, font=rcfg.get("font", "Anton"), credit=credit,
                                 cta=cta, cta_seconds=float(preset.get("cta_seconds", 3.5)))
        ass_path = captions.write_ass(clips_dir / f"{name}.ass", ass)
    out = clips_dir / f"{name}.mp4"
    render.render_clip(src, offset, end - start, out, rcfg, ass_path)
    return render.thumbnail(out, max(0.0, thumb_at), clips_dir / f"{name}.jpg")


MIN_TRIM, MAX_TRIM = 3.0, 180.0   # YouTube Shorts can be up to 3 minutes


def rerender_clip(job_dir: Path, name: str, start: float, end: float, reporter: Reporter | None = None) -> dict:
    """Cut one clip again at [start, end] on the SOURCE timeline (VOD time; stream time for live clips).

    Downloads just that section (+ a few seconds of lead-in for Whisper), re-transcribes it so the
    captions match, and re-renders with the job's layout, credit and CTA. User edits to the title
    are kept; the source link in the description moves to the new start."""
    rep = reporter or Reporter()
    if not MIN_TRIM <= end - start <= MAX_TRIM:
        raise PipelineError(f"A clip must be {MIN_TRIM:.0f}–{MAX_TRIM:.0f} seconds long (got {end - start:.1f} s).")
    if start < 0:
        raise PipelineError("Start can't be before the beginning of the video.")
    manifest = json.loads((job_dir / "manifest.json").read_text())
    clips_dir = job_dir / "clips"
    clip = json.loads((clips_dir / f"{name}.json").read_text())
    settings, info, entry = manifest["settings"], manifest["info"], manifest.get("streamer") or {}
    job_offset = float(manifest.get("stream_offset", clip.get("stream_start", clip["start"]) - clip["start"]) or 0)
    source = manifest["source"]
    work = job_dir / f"work_{name}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        local = None if ingest.is_url(source) else Path(source)
        if local is not None and local.exists():           # the original file is still here
            src, base = local, job_offset                  # file time = source time - job_offset
        else:
            remote = source if ingest.is_url(source) else info.get("webpage_url")
            if not remote:
                raise PipelineError("The uploaded file was deleted after clipping. Upload it again to trim this clip.")
            ingest.probe(remote, settings)   # clear error if a live stream hasn't become a VOD yet
            lead = 8.0
            sec0 = max(0.0, start - lead)
            rep("render", 0.1, f"Downloading {_fmt(start)}–{_fmt(end)}…")
            src = ingest.download_section(remote, sec0, end + 1.0, work / "src.mp4", settings)
            base = sec0
        s, e = start - base, end - base                   # times inside `src`
        words: list[dict] = []
        cap_cfg = settings["captions"]
        warning = None
        if cap_cfg.get("enabled", True):
            rep("transcribe", 0.4, "Transcribing for captions…")
            try:
                segs = _transcribe(src, max(0.0, s - 4.0), e + 0.5, cap_cfg)
                words = [w for sg in segs for w in sg.get("words", []) if s <= w["start"] < e]
            except Cancelled:
                raise
            except Exception as exc:
                warning = f"captions unavailable ({exc})"
        rep("render", 0.7, f"Rendering {name} ({e - s:.0f}s)…")
        thumb = _burn(src, s, s, e, words, clips_dir, name, settings, entry, thumb_at=min(3.0, (e - s) / 2))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    old_link = clip.get("source_link") or ""
    new_link = metadata.timestamped_url(info.get("webpage_url"), start)
    clip.update({
        "start": round(start - job_offset, 2), "end": round(end - job_offset, 2), "duration": round(end - start, 2),
        "transcript": " ".join(w["word"] for w in words) if cap_cfg.get("enabled", True) else clip.get("transcript", ""),
        "source_link": new_link,
        "thumbnail": f"clips/{name}.jpg" if thumb else clip.get("thumbnail"),
        "trimmed": True,
        "rendered_at": time.time(),
    })
    if manifest.get("is_live_chunk") or "stream_start" in clip:
        clip["stream_start"] = round(start, 2)
    if old_link and old_link in (clip.get("description") or ""):
        clip["description"] = clip["description"].replace(old_link, new_link)
    clip.pop("warning", None)
    if warning:
        clip["warning"] = warning
    (clips_dir / f"{name}.json").write_text(json.dumps(clip, indent=2, ensure_ascii=False))
    rep("done", 1.0, f"{name} re-cut to {_fmt(start)}–{_fmt(end)} ({end - start:.0f}s)")
    return clip


def _transcribe(media: Path, start: float, end: float, cap_cfg: dict) -> list[dict]:
    from .transcribe import transcribe_window

    return transcribe_window(media, start, end, cap_cfg.get("model", "base"), cap_cfg.get("language"))


def _fmt(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"


def _json_default(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    raise TypeError(type(o))
