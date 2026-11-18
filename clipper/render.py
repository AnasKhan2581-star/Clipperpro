"""ffmpeg rendering: vertical/landscape layouts, burned captions, loudness normalization."""
from __future__ import annotations

import subprocess
from pathlib import Path

W, H = 1080, 1920


def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def layout_filter(layout: str, cfg: dict) -> tuple[str, int, int]:
    """Return (filter graph from [0:v] to [base], width, height)."""
    if layout == "landscape":
        g = ("[0:v]scale=1920:1080:force_original_aspect_ratio=decrease:flags=lanczos,"
             "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1[base]")
        return g, 1920, 1080
    if layout == "crop":
        g = f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},setsar=1[base]"
        return g, W, H
    if layout == "facecam":
        x, y, w, h = cfg.get("facecam_box", [0.0, 0.62, 0.28, 0.38])
        cam_h = _even(H * float(cfg.get("facecam_height_ratio", 0.36)))
        game_h = H - cam_h
        g = (
            "[0:v]split=2[c][g];"
            f"[c]crop=iw*{w:.4f}:ih*{h:.4f}:iw*{x:.4f}:ih*{y:.4f},"
            f"scale={W}:{cam_h}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{cam_h},setsar=1[cam];"
            f"[g]scale={W}:{game_h}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{game_h},setsar=1[game];"
            "[cam][game]vstack=inputs=2[base]"
        )
        return g, W, H
    # default: blur — full 16:9 frame centered over a blurred, zoomed copy
    g = (
        "[0:v]split=2[a][b];"
        f"[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=12:2,"
        f"scale={W}:{H},eq=brightness=-0.10:saturation=1.25[bg];"
        f"[b]scale={W}:-2:flags=lanczos[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[base]"
    )
    return g, W, H


def output_size(layout: str) -> tuple[int, int]:
    return (1920, 1080) if layout == "landscape" else (W, H)


def render_clip(src: Path, offset: float, duration: float, out: Path, render_cfg: dict,
                ass_file: Path | None = None) -> Path:
    """Cut [offset, offset+duration] of src, apply layout + captions, encode H.264/AAC."""
    layout = render_cfg.get("layout", "blur")
    graph, _w, _h = layout_filter(layout, render_cfg)
    if ass_file is not None:
        # run with cwd=out.parent and a bare filename to avoid filter-path escaping issues
        fonts = next((d for d in ("/usr/share/fonts", "/Library/Fonts", "C:/Windows/Fonts") if Path(d).is_dir()), None)
        fontsdir = f":fontsdir={fonts}" if fonts and ":" not in fonts else ""
        graph += f";[base]ass={ass_file.name}{fontsdir},format=yuv420p[v]"
    else:
        graph += ";[base]format=yuv420p[v]"
    cmd = [
        "ffmpeg", "-v", "error", "-nostdin", "-y",
        "-ss", f"{max(0.0, offset):.3f}", "-i", str(src.resolve()), "-t", f"{duration:.3f}",
        "-filter_complex", graph, "-map", "[v]", "-map", "0:a:0?",
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000",
        "-c:v", "libx264", "-preset", str(render_cfg.get("preset", "veryfast")),
        "-crf", str(render_cfg.get("crf", 20)), "-profile:v", "high", "-r", str(int(render_cfg.get("fps", 30))),
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
        out.name,
    ]
    proc = subprocess.run(cmd, cwd=out.parent, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg render failed: {proc.stderr.strip()[-800:]}")
    return out


def thumbnail(video: Path, at: float, out: Path) -> Path | None:
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", f"{max(0.0, at):.2f}", "-i", str(video),
           "-frames:v", "1", "-vf", "scale=360:-2", "-q:v", "4", str(out)]
    proc = subprocess.run(cmd, capture_output=True)
    return out if proc.returncode == 0 and out.exists() else None
