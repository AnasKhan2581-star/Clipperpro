# Clipperpro: instructions for Claude

**Read `docs/HANDOFF.md` first.** It holds the full context of this project, including:
- the owner's setup (Windows, Docker, India)
- the active Tomo GTA VI clipping campaign and all its rules
- what is verified, the bugs already fixed, and the advice already given
- the next steps

## Project in one paragraph
A free, self-hosted pipeline that turns long GTA VI streams into captioned vertical Shorts.
It uses yt-dlp to probe, fetch audio only and download just the clip sections. Loudness,
loudness rise and chat signals produce a hype score. Whisper runs on the candidates only,
to rerank them and snap clip edges to sentences. ffmpeg renders 9:16 layouts with burned-in
word-by-word captions, a credit line and an optional CTA card. Templated titles and
descriptions follow, then an optional YouTube upload. A FastAPI web UI runs on :8000
(Docker), alongside a CLI and GitHub Actions.

## Commands
- Run the app: `docker compose up -d --build`, then open http://localhost:8000
- Tests: `pip install -r requirements-dev.txt && python -m pytest -q` (24 tests)
- CLI: `python -m clipper run <url|file> --clips 5 --layout facecam`

## Rules
- Respect the streamer allowlist (`config/streamers.yaml`). Only add streamers with a real permission source.
- Per-streamer campaign rules live in the allowlist entry's `preset:`, as the Tomo entry shows.
- Never commit anything in `data/`, or any cookies or OAuth files.
- Don't post, submit links, create accounts or move money for the owner. Guide them instead.
