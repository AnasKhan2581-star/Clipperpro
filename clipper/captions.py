"""Generate animated word-by-word captions as an ASS subtitle file."""
from __future__ import annotations

import re
from pathlib import Path


def _ts(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _esc(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def chunk_words(words: list[dict], per_line: int = 3, max_chars: int = 20, max_gap: float = 0.7) -> list[list[dict]]:
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        if cur:
            gap = w["start"] - cur[-1]["end"]
            chars = sum(len(x["word"]) + 1 for x in cur) + len(w["word"])
            ends_sentence = bool(re.search(r"[.!?]$", cur[-1]["word"]))
            if len(cur) >= per_line or chars > max_chars or gap > max_gap or ends_sentence:
                chunks.append(cur)
                cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def build_ass(words: list[dict], clip_start: float, clip_end: float, cfg: dict, *,
              width: int = 1080, height: int = 1920, font: str = "Anton",
              credit: str | None = None) -> str:
    """`words` use absolute VOD time; output is relative to the clip start."""
    cap = cfg or {}
    size = int(cap.get("font_size", 86) * (width / 1080 if width < height else height / 1920 * 1.0))
    if width > height:  # landscape: smaller captions
        size = int(cap.get("font_size", 86) * 0.75)
    margin_v = int(height * (1 - float(cap.get("position", 0.70))))
    text_c = cap.get("text_color", "&H00FFFFFF")
    hi_c = cap.get("highlight_color", "&H0000E5FF")
    upper = bool(cap.get("uppercase", True))
    credit_size = max(28, int(size * 0.42))

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font},{size},{text_c},{text_c},&H00000000,&H96000000,-1,0,0,0,100,100,1,0,1,7,3,2,60,60,{margin_v},1
Style: Credit,{font},{credit_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,1,0,1,4,2,8,40,40,{int(height * 0.06)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events: list[str] = []
    duration = clip_end - clip_start
    if credit:
        events.append(f"Dialogue: 1,{_ts(0)},{_ts(duration)},Credit,,0,0,0,,{_esc(credit)}")

    rel = []
    for w in words:
        s, e = w["start"] - clip_start, w["end"] - clip_start
        if e <= 0 or s >= duration:
            continue
        word = _esc(w["word"].upper() if upper else w["word"])
        rel.append({"start": max(0.0, s), "end": min(duration, e), "word": word})

    per_line = int(cap.get("words_per_line", 3))
    for chunk in chunk_words(rel, per_line=per_line):
        for i, w in enumerate(chunk):
            start = w["start"]
            end = chunk[i + 1]["start"] if i + 1 < len(chunk) else max(w["end"], start + 0.25)
            if end <= start:
                continue
            parts = []
            for j, other in enumerate(chunk):
                if j == i:
                    # pop + highlight the word being spoken
                    parts.append("{\\c" + hi_c + "\\fscx112\\fscy112\\t(0,90,\\fscx100\\fscy100)}" + other["word"] + "{\\r}")
                else:
                    parts.append(other["word"])
            events.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Caption,,0,0,0,,{' '.join(parts)}")
    return header + "\n".join(events) + "\n"


def write_ass(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path
