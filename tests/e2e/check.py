"""Assertions for the end-to-end workflow: python tests/e2e/check.py <outdir> <mode>"""
import json
import subprocess
import sys
from pathlib import Path

out, mode = Path(sys.argv[1]), sys.argv[2]
m = json.loads((out / "manifest.json").read_text())
clips = m["clips"]
assert clips, "no clips produced"
s = m["settings"]["clips"]
for c in clips:
    p = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height", "-show_entries",
         "format=duration", "-of", "json", str(out / c["file"])], capture_output=True, text=True, check=True).stdout)
    v = next(x for x in p["streams"] if x["codec_type"] == "video")
    assert any(x["codec_type"] == "audio" for x in p["streams"]), f"{c['name']}: no audio"
    expected = (1920, 1080) if m["settings"]["render"]["layout"] == "landscape" else (1080, 1920)
    assert (v["width"], v["height"]) == expected, f"{c['name']}: size {v['width']}x{v['height']}"
    dur = float(p["format"]["duration"])
    assert s["min_seconds"] - 1 <= dur <= s["max_seconds"] + 1, f"{c['name']}: duration {dur}"
    print(f"✔ {c['name']} {dur:.1f}s score={c['score']} hits={c['keyword_hits']} hook={c['hook']!r}")
    print(f"   title: {c['title']}")
    print(f"   transcript: {c['transcript'][:160]!r}")

if mode == "captions":
    best = clips[0]
    assert best["start"] <= 61 and best["end"] >= 64, f"speech moment not selected: {best['start']}-{best['end']}"
    assert best["keyword_hits"] >= 2, f"keywords not detected: {best['transcript']!r}"
    ass = (out / "clips" / f"{best['name']}.ass").read_text()
    assert ass.count("Caption,,") >= 5, "captions missing from subtitle file"
    assert "COPS" in ass or "CHASING" in ass, "spoken words not in captions"
elif mode == "download":
    assert all(c["source_link"] and "t=" in c["source_link"] for c in clips), "missing timestamped source link"
print(f"\nE2E {mode}: all checks passed ({len(clips)} clips)")
