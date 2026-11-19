# Handover: full context of the Clipperpro project

This file carries everything from the first (cloud) Claude session into new sessions,
for example Claude Code running on the owner's Windows PC. **Read all of it before
doing anything.** Written 2026-11-19, one day after GTA VI launched (2026-11-18).

---

## 1. Goal

The owner (GitHub: `AnasKhan2581-star`, based in **India**) wants to make money and
grow a YouTube Shorts channel by clipping famous streamers' **GTA VI** streams fast,
with an automated pipeline instead of clipping by hand. The original request was a
"pipeline webapp, unlimited free use, built like a pro, hosted in a new GitHub repo".

The current concrete goal is to **earn from the Clipping Nation "Tomo GTA VI Clipping"
campaign** on Content Rewards (Whop), posting to **YouTube Shorts + Instagram Reels**.

---

## 2. Where things are

| Thing | Location |
|---|---|
| Repo | https://github.com/AnasKhan2581-star/Clipperpro (public, branch `main`) |
| Local copy on the owner's PC | `D:\Clipperpro-main` (extracted from the ZIP; the folder containing `docker-compose.yml`). If it's nested, it may be `D:\Clipperpro-main\Clipperpro-main`. |
| App, while running | http://localhost:8000 (Docker) |
| User guide (click-by-click) | `docs/GETTING_STARTED.md` |
| Phased future plan | `docs/ROADMAP.md` |
| Settings | `config/settings.yaml` (re-read on every job, no restart needed) |
| Streamer allowlist + campaign presets | `config/streamers.yaml` |
| Watch list for auto-clipping | `config/watchlist.yaml` (currently empty) |
| Jobs, clips, DB, YouTube token | `data/` (in Docker it's mounted at `/app/data`) |

The local copy is **not a git clone** (it came from a ZIP). To update it, download a
fresh ZIP and re-extract, or download single files with
`curl -L -o <path> https://raw.githubusercontent.com/AnasKhan2581-star/Clipperpro/main/<path>`.

---

## 3. The owner's setup (state on 2026-11-19)

- **Windows 10/11**, Command Prompt. Use `cd /d D:\...` to change drives; a plain `cd D:\...` doesn't.
- **Docker Desktop installed and working.** It first failed with "Virtual Machine Platform
  not enabled"; fixed with `wsl --install` in an admin PowerShell plus a restart.
- `docker compose up -d --build` has run, and **the app is running** at localhost:8000.
- **Whop joined, Clipping Nation Discord joined.**
- **Not yet done:**
  - read the campaign's Approval Guide and Tier 1 guide
  - create dedicated clip accounts
  - warm up the accounts
  - connect the accounts in Whop
  - make, post and submit the first clips
  - YouTube upload API setup (optional)
- Avoid OneDrive folders (the Desktop is inside OneDrive): big video files would sync to the cloud.

---

## 4. What the app does (architecture)

Python 3.12 + FastAPI + ffmpeg + yt-dlp + faster-whisper. No paid APIs.

Pipeline (`clipper/pipeline.py`):
1. **Probe** the URL with yt-dlp, then run the **allowlist check** (`config/streamers.yaml`, `enforce: true`).
   Local file uploads skip enforcement.
2. **Download audio only** (small), or use the local file.
3. **Signals at 1 Hz** (`analysis.py`):
   - loudness vs a rolling 5-minute baseline
   - loudness rise
   - chat-rate (YouTube live-chat replay via yt-dlp; Twitch only if `chat-downloader` is installed)
   - optional scene cuts
4. **Hype score** and greedy peak picking with non-max suppression (`highlights.py`), keeping 3× the candidates.
5. **Whisper on candidates only** (`transcribe.py`):
   - keyword rerank; digits are normalized, so "5 stars" matches "five stars"
   - snap clip edges to sentence boundaries
   - pick a "hook" sentence
6. **Download only the clip sections** (yt-dlp `download_ranges` + `force_keyframes_at_cuts`,
   frame-accurate). If a host can't seek, it **falls back** to one full download.
7. **Render** (`render.py`):
   - layouts `blur` (16:9 over blurred fill), `facecam` (cam stacked on game), `crop`, `landscape`
   - 1080×1920, 30 or 60 fps
   - loudnorm to −14 LUFS
   - burned-in ASS captions (`captions.py`): word-by-word highlight, credit line at top,
     optional **CTA end card**
8. **Metadata** (`metadata.py`):
   - title and description from templates, with a timestamped source link
   - `{hashtags}` = required tags + a rotating sample of a pool
9. **YouTube upload** (`youtube.py`, optional): the owner's own Google OAuth client, about 6 uploads/day on the free quota.

**Streamer presets.** An allowlist entry can carry `preset:` with settings overrides plus
`credit_label`, `cta` and `cta_seconds`. Precedence: base settings < streamer preset < job options.
Web UI saves keep existing presets.

**Live mode** (`live.py`, added 2026-11-19): "🔴 Clip while live" in the UI, or `python -m clipper live <url>`.
- ffmpeg records the live HLS (video + audio playlists, `-copyts`, stream copy) into 1-minute `.ts` segments.
  On YouTube the copied timestamps equal the position in the stream, so clips get exact `?t=` links.
- Every `live.chunk_minutes` (20), segments + 60 s of context are joined into a chunk MP4 and queued as a
  normal local-file job with `live` options: the stream's info, its offset, and a **peak window**.
  Windows of consecutive chunks meet exactly, so no moment is clipped twice.
- Reconnects when the stream URL expires; overlapping segments after a reconnect are trimmed (`inpoint`).
  The session ends when the stream ends or on Stop/cancel, clipping the last part too.
- The session itself is a job row (`options.mode = "live"`, run by `LiveManager` on its own thread, never queued);
  chunk jobs carry `options.live_session`. A quiet chunk ends as "done" with no clips (`NoMoments`).

Other parts:
- `jobs.py`: SQLite store, worker threads, `LiveManager`
- `server.py`: REST API and static UI from `web/`; optional `CLIPPER_PASSWORD` basic auth
- `watch.py`: channel watcher
- `cli.py`: `python -m clipper run|serve|upload|auth-youtube|watch`

GitHub Actions:
- `clip.yml`: manual "Clip a VOD" run
- `watch.yml`: every 3 hours
- `ci.yml`: pytest (32 tests) + Docker build
- `e2e.yml`:
  - real Whisper on espeak speech
  - URL section download + fallback via a local HTTP server
  - a real Twitch VOD smoke test

---

## 5. Verified vs not verified

| Part | Status |
|---|---|
| Detection, rendering, all layouts, captions, credit, CTA card | ✅ tests + visual checks |
| Real Whisper transcription | ✅ E2E: heard "The cops are chasing us. 5 stars. Run, run, let's go", picked that moment and titled the clip from it |
| Section downloads | ✅ frame-identical to full-file cuts (PSNR 48 dB); fallback works |
| Twitch | ✅ real 9.3 h Twitch VOD: metadata, credit link, 1080p section with one audio track |
| **YouTube as a source** | ✅ Works from the owner's home PC without cookies (2026-11-19: Tomo's 3 h "PLAYING GTA 6 FOR THE FIRST TIME", 5 clips, ~6 min). Still blocked from GitHub/datacenter IPs; if it ever says "Sign in to confirm you're not a bot", use cookies (§9). |
| Tomo facecam box | ✅ Measured: `[0.70, 0.43, 0.30, 0.315]` (bottom-right, under chat), saved in the Tomo preset. Re-measure if he moves his cam. |
| Live mode | ✅ 32 tests incl. a fake-live recording; started on Tomo's real Day 2 stream 2026-11-19 (see §8). |
| YouTube upload API | ⏳ not tested (needs the owner's Google login) |

Bugs found and fixed during debugging (so you don't reintroduce them):
1. faster-whisper's file decoding calls PyAV with `metadata_errors`, which new PyAV removed, so
   **every transcription failed silently**. Fix: decode with ffmpeg and pass a float32 numpy array.
   Caption failures now show as **warnings** in the UI and CLI.
2. Uploads ignored the job's privacy setting. Fixed: they use the manifest's settings.
3. Direct links have no duration in yt-dlp. Fixed: ffprobe fallback.
4. A host without HTTP Range failed section downloads. Fixed: full-download fallback.
5. Twitch downloaded audio twice. Fixed with the format string `bv[height<=H]+ba/b[height<=H]/bv*+ba/b`.
6. Others:
   - cancel during download now shows "cancelled"
   - large uploads no longer block the event loop
   - the Twitch credit URL fallback
   - apt stalls on GitHub runners: `.github/actions/setup/apt.sh`

---

## 6. The active campaign: Tomo GTA VI Clipping (Clipping Nation)

Campaign page: https://contentrewards.com/c/campaigns/df6de26f-86ec-4782-b818-1fc8053b99a4
(The cloud session's network blocked this site; the owner pasted the brief.)

| Payout | Min views | Max per video | Budget |
|---|---|---|---|
| $1.50 / 1K views | 2,333 (= $3.50) | $150 (≈100K views) | $1,000 total, shared by all clippers (about $390 used per third-party sites; may run out) |

Content Rewards takes a **10% fee**. Clips earn for 30 days after submission.

**Rules (from the brief):**
- **Source:** clips must feature Tomo and come mainly from his **GTA VI YouTube livestreams** or GTA VI videos.
  His channel is youtube.com/@Tomovids; full VODs are on the **Live** tab.
- **CTA:** every clip mentions Tomo and tells viewers where to watch more ("subscribe to Tomo on YouTube").
- **Platforms:** TikTok, Instagram Reels and YouTube Shorts only. Cross-posting is encouraged.
  The **owner skips TikTok** because it is **banned in India**.
- **Audience:** English, and **at least 40% Tier 1 (USA, UK, Canada, Australia)**. After 2,333 views, send
  a screenshot or recording of the audience country breakdown to the **Discord support chat**.
  **No proof means no approval.**
- **Captions:** use **#tomovids** and **#gta6**, and mention **@Tomovids** on screen or in the caption.
  Captions must be readable without sound.
- **Likes must be visible**; hidden likes get rejected.
- **Account:** must be in a related niche (gaming, GTA, streamers, clips).
- **Engagement** at or above 1%.
- **Keep every clip live for at least 30 days.**
- **Originality:**
  - original edits only
  - no stolen or reuploaded clips, no 1:1 copies
  - never more than 5 near-identical posts
- **No** engagement bait, misleading titles, fake context, clickbait thumbnails, or negative portrayal of Tomo.
- **No AI:** no AI-generated thumbnails or AI-generated content.
  *Open question: ask in Discord whether auto-captions and auto-clipping tools are OK. The app transcribes
  Tomo's real words and doesn't generate content, but they decide.*
- **No fake engagement:** botting, buying or boosting means a permanent ban from all Clipping Nation campaigns.
- **Submit the link within 30 minutes of posting.**
- **Required reading:** the Approval Guide, and How to Target a Tier 1 Audience (the owner is outside Tier 1).
- **No low-effort content:** no raw screen recordings or random moments. One great clip beats ten rushed ones.

**Video structure:**
1. Hook in the first 2 seconds
2. One clear moment (funny, chaotic, a first reaction, stunts/chases/fails, player interactions)
3. CTA

**Hashtags:** mix these between posts: #tomo #tomovids #gta6 #gtavi #gta #gaming #funnymoments

**Accounts:**
- fresh dedicated accounts, named like TomoClips, TomoGTA6 or TomoMoments
- the campaign thumbnail as profile picture
- follow the **Account Warm-Up Guide** before the first post

**Getting paid:**
1. Join the Clipping Nation Whop, then open the Content Rewards app.
2. Join the Discord.
3. Connect your socials in Whop (Settings → Connected Accounts).
4. Submit each post within 30 minutes.
5. Send the analytics proof after 2,333 views.
6. After approval, the payout queues automatically.

**Already configured in `config/streamers.yaml` (Tomo preset):**
- the `@Tomovids` credit at the top
- the "SUBSCRIBE TO TOMO ON YOUTUBE @Tomovids" CTA card in the last 3.5 s
- `#tomovids #gta6` in the title, plus 3 rotating tags in the description
- "Subscribe to Tomo on YouTube 👉 @Tomovids" in the description
- English captions
- `pre_roll: 0.2` so the peak comes early (hook)

---

## 7. Advice already given (don't contradict it without reason)

**Permission comes first.**
- Only clip streamers whose rules or campaign allow it, and record where the permission comes from in the allowlist.
- None of the big names (IShowSpeed, Kai Cenat, Mythpat, xQc) have public blanket permission.
- Clipping without permission means Content ID claims (revenue goes to them) or strikes (3 strikes and the channel is deleted).

**Biggest GTA VI launch streams (launch numbers were still provisional, and trackers disagree):**
- HoneyMad (Russian, about 576–642K)
- Rubius (Spanish, about 311K)
- IShowSpeed (about 143K on YouTube, in one snapshot)
- Mythpat (Indian, 125K+)
- Kai Cenat (about 30.5K snapshot; huge clip ecosystem)
- If the owner ever targets India: Mythpat (Hindi) is likely much less crowded with clippers. But he has no known campaign.

**Other campaigns found (re-check the live pages; pools run out fast and sources disagreed):**
- Lacy GTA VI marathon pool, $12.5K (Reach, Content Rewards)
- Clix pool, $5K
- Kai Cenat GTA 6 pool, $10K (Clipped & Shipped; 750K approved-view minimum; unconfirmed)
- IShowSpeed × Expedia: $1/1K, mostly spent
- KreekCraft (ClipLaunch): $1/1K, max $300
- Lysium / Benji Ramos (GTA roleplay): $1.50/1K
- Dasgasd3m: $1/1K
- Tokyo Girl (music promo): $0.70/1K
- N3on (Kick): $0.40/1K, hard to join
- Platforms: Content Rewards, clipping.net, Vyro, Lumina, Vore India (₹ campaigns)
- **Expect a real blended rate around $0.39 per 1,000 views.** Never pay a fee to join a "program".

**VPN question.** Advice given: don't use a VPN or fake location.
- YouTube decides audience from content and viewer behavior, not upload IP.
- Switching locations can lock a new account.
- "Country of residence" is tied to tax/AdSense, so keep it truthful (India).
- TikTok is banned in India, so skip it.

**What actually helps the 40% Tier 1 requirement:**
- everything in English
- **don't share clips with Indian friends or WhatsApp groups** (early viewers seed the audience)
- warm the account up on US gaming content
- post at US evening time, 6–10 PM ET ≈ 4:30–8:30 AM IST (schedule in YouTube Studio, but submit within 30 minutes of going live)
- check Analytics → Audience → Top geographies after a few clips
- read the campaign's Tier 1 guide, and ask in Discord if unsure

---

## 8. Next steps (in order)

1. Read the campaign's Approval Guide and Tier 1 guide. Ask in Discord about auto-caption tools.
2. Create the dedicated **YouTube** channel and **Instagram** account (TomoClips/TomoGTA6), with the campaign thumbnail as profile picture.
3. Do the warm-up. Make sure **like counts are visible**.
4. Connect the accounts in Whop (Settings → Connected Accounts).
5. ✅ Done 2026-11-19 (Claude Code on the owner's PC):
   - VOD "PLAYING GTA 6 FOR THE FIRST TIME": jobs `20261119-165029-abdae4` (blur) and `20261119-170659-4be88e` (facecam).
     Suggested keepers: clip_02 (the mission reaction) and clip_03 (if the visual pays off), maybe clip_01. Drop clip_04 (mostly
     cutscene dialogue) and clip_05 (his outro/giveaway plug; a misleading title).
   - Live mode built, then started on Day 2 (`uDwiE9saR0c`): session `20261119-180117-e99e44`.
   - From now on: for a stream that's live, tick **🔴 Clip while live**. For a finished one, paste the VOD with layout **facecam**
     (the Tomo cam box is in the preset).
6. **Review like an editor:**
   - keep 2–3 clips
   - hook in 2 seconds, one clear moment
   - fix Whisper mistakes (e.g. "Clip hat" for "clip that")
   - make the title honest, never negative about Tomo
7. Post to YouTube Shorts + Instagram Reels, **publicly**, from the connected accounts. Submit each link within **30 minutes**.
8. After 2,333 views, send the country-breakdown proof in the Discord support chat.
9. Weekly: check which clips earned and the remaining budget. When it runs out, move to another campaign.
10. Later (ROADMAP Phase 2+, ask Claude one at a time):
    - trim editor + re-render
    - a hook-title overlay
    - scheduled publishing
    - Discord notifications
    - face-tracking crop

**The owner does these themselves (they're in their name and need verification):**
- creating accounts
- the warm-up
- posting
- submitting links
- sending analytics proof
- anything involving payments

---

**Note (2026-11-19):** the owner added a local-only "Kai cenat" allowlist entry (permission note: "allowed personally").
No public blanket permission is known for Kai Cenat (§7), so keep proof of that OK (the message or screenshot) in case
of a Content ID claim or strike. The entry is not in the repo's `config/streamers.yaml`.

## 9. Troubleshooting (Windows)

| Problem | Fix |
|---|---|
| "no configuration file provided" | You're in the wrong folder. `cd /d D:\Clipperpro-main`, then `dir` must show `docker-compose.yml` (it may be one level deeper). |
| A path containing `.zip\` | The ZIP wasn't extracted. Right-click → Extract All → `D:\` |
| Docker "Virtual Machine Platform" | Admin PowerShell: `wsl --install`, restart, reinstall Docker (All-users). Virtualization errors mean enabling VT-x/SVM in BIOS. |
| YouTube "Sign in to confirm you're not a bot" | 1. Export cookies with the browser extension "Get cookies.txt LOCALLY" to `D:\Clipperpro-main\data\cookies.txt`. 2. In `config/settings.yaml` set `download: cookies_file: /app/data/cookies.txt`. 3. Retry. |
| "not in your streamer allowlist" | Add the streamer in Streamers & settings (only with permission). |
| Caption warnings (⚠) in a job | The Whisper model download failed or similar. Check the logs; the first run downloads the model (~150 MB for `base`). |
| Update the app | Download a fresh ZIP, extract, then `docker compose up -d --build`. `data/` and `config/` hold the state, so keep your edited `config/` files. |
| Plan B without Docker | `winget install Python.Python.3.12`, `winget install Gyan.FFmpeg`, then `python -m venv .venv`, `.venv\Scripts\activate`, `pip install -r requirements.txt`, `python -m clipper serve` |

Useful commands:
```
docker compose up -d --build     # start / rebuild
docker compose logs -f           # watch logs
docker compose down              # stop
docker compose run --rm --service-ports clipper auth-youtube   # one-time YouTube upload auth
```

---

## 10. Ground rules for any Claude session helping here

- **Never** add a streamer to the allowlist without a real permission source (a campaign brief or a written OK).
- **Never** suggest botting, buying or boosting views, a fake location or residence, or getting around the TikTok ban.
- Don't post, submit, create accounts, or move money on the owner's behalf; guide them instead.
- Treat third-party clipping blogs as unreliable. Verify campaigns on the live Content Rewards page.
- Keep commits out of `data/` (tokens, cookies, clips). Never commit `client_secret.json`, `youtube_token.json` or `cookies.txt`.
- Run `python -m pytest -q` before committing code changes. CI and E2E run on every push to `main`.
