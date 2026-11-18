#!/usr/bin/env bash
# Build a fake 2-minute "stream": quiet background + a loud spoken hype moment at 1:00.
# usage: tests/e2e/make_vod.sh <out.mp4>
set -euo pipefail
out="$1"; tmp="$(mktemp -d)"
espeak-ng -s 140 -a 200 -w "$tmp/speech.wav" "No way bro! The cops are chasing us. Five stars! Run, run! Let's go, clip that!"
ffmpeg -v error -y -f lavfi -i testsrc2=size=1280x720:rate=30:duration=120 \
  -f lavfi -i "anoisesrc=d=120:c=pink:a=0.015" -i "$tmp/speech.wav" \
  -filter_complex "[2:a]adelay=60000|60000,volume=3[s];[1:a][s]amix=inputs=2:normalize=0:duration=first[a]" \
  -map 0:v -map "[a]" -c:v libx264 -preset ultrafast -c:a aac -movflags +faststart "$out"
