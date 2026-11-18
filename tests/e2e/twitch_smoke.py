"""Smoke-test the Twitch path on a real public VOD: metadata, credit link, formats, 20s section."""
import json
import subprocess
import sys
from pathlib import Path

from clipper import ingest
from clipper.config import load_settings
from clipper.watch import latest_videos

settings = load_settings()
url = sys.argv[1] if len(sys.argv) > 1 else None
if not url:
    vids = [v for v in latest_videos("https://www.twitch.tv/twitch/videos?filter=archives", 5, settings) if v["url"]]
    if not vids:
        print("no public VODs found to test with; skipping"); raise SystemExit(0)
    url = vids[0]["url"]
info = ingest.probe(url, settings)
print(json.dumps({k: info[k] for k in ("id", "title", "uploader", "uploader_id", "channel_url", "duration", "extractor")}, indent=2))
assert info["duration"] > 60 and info["uploader"], "metadata incomplete"
assert info["channel_url"] and "twitch.tv" in info["channel_url"], "no credit link"
start = min(600.0, info["duration"] / 2)
out = ingest.download_section(url, start, start + 20, Path("out/twitch/section.mp4"), settings)
dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                           capture_output=True, text=True).stdout)
streams = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,height", "-of", "csv=p=0", str(out)],
                         capture_output=True, text=True).stdout.split()
print("section:", round(dur, 2), "s", streams)
assert 18 <= dur <= 23, f"section length {dur}"
assert sum(1 for s in streams if s.startswith("audio")) == 1, "expected exactly one audio track"
print("Twitch smoke test passed")
