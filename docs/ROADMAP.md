# Roadmap: from first clip to a growing Shorts channel

Each phase has a **goal**, **steps**, and a **"done when"** check so you know when to move on.
✅ = already built · 🔧 = to build (each is a small, separate task; ask Claude for them one at a time).

---

## Phase 0: First real clips (days 1–3)

**Goal:** prove the pipeline on real GTA VI streams and tune it for your streamers.

1. Pick **2–3 streamers** who allow clip channels. Note where their permission comes from.
2. Add them to the allowlist ([GETTING_STARTED](GETTING_STARTED.md), Step 1).
3. Run **one VOD per streamer** with 5 clips.
4. For each streamer, write down:
   - the facecam position (which corner), so you can reuse the `facecam_box`
   - which layout looks best (`facecam` or `blur`)
   - how many of the 5 clips were actually good (your hit rate)
5. If clips start too late or end too early, adjust `clips.pre_roll` in `config/settings.yaml`: higher means more lead-up.
6. If good moments are missed, raise `detection.candidate_multiplier` from 3 to 5, so it checks more candidates.

**Done when:** at least 3 of every 5 clips are worth posting.

---

## Phase 1: Launch the channel (week 1–2)

**Goal:** a consistent, credible clip channel.

1. **Channel setup:** a name about GTA VI clips, a banner, and an About section saying you clip *with credit and permission*, with a contact email for takedown requests.
2. **Posting rhythm:** 3–6 Shorts per day (the free upload quota is about 6/day). Same times every day.
3. **Upload as private, then schedule.** Review each clip in YouTube Studio before it goes public.
4. **Titles:** the auto title is a starting point. Rewrite the best ones with a hook, for example *"xStreamer gets 5 stars in 10 seconds 😭"*.
5. **Track** in a simple sheet: clip, streamer, upload date, views after 48 h, likes.
6. **Weekly review:** which streamers and moment types (chases, crashes, funny NPCs) win? Do more of those.

**Done when:** 2 weeks of daily posting, and you know your top 2 streamers and moment types.

---

## Phase 2: Quality upgrades (weeks 2–4)

**Goal:** clips that look hand-edited.

| # | Feature | Why it matters | Status |
|---|---|---|---|
| 2.1 | Highlight detection, captions, layouts, credit | the core | ✅ |
| 2.2 | 30/60 fps option | smoother driving footage | ✅ |
| 2.3 | **Per-streamer presets**: save layout + facecam box in the allowlist entry | no retyping per VOD | 🔧 |
| 2.4 | **Trim editor**: nudge a clip's start/end in the web app and re-render | fix the 1-in-5 clips that cut early | 🔧 |
| 2.5 | **Re-render button** (different layout/captions) without re-downloading | try variations quickly | 🔧 |
| 2.6 | **Hook text** in the first 2 s (big title at the top) | Shorts retention is won in 2 s | 🔧 |
| 2.7 | **Auto-zoom punch-in** at the peak moment | adds energy | 🔧 |
| 2.8 | **Face tracking** (auto-find the facecam, no manual box) | zero setup per streamer | 🔧 |
| 2.9 | **Emoji/keyword highlights** in captions (e.g. "COPS" 🚓 in red) | more polish | 🔧 |
| 2.10 | **Auto-generated thumbnail** frame picker | better CTR on long-form | 🔧 |

Suggested order: 2.3, then 2.4, 2.6 and 2.5. These four give the biggest visible jump.

**Done when:** most clips go out without manual editing.

---

## Phase 3: Hands-off automation (month 2)

**Goal:** clips appear and get scheduled without you starting anything.

| # | Feature | Status |
|---|---|---|
| 3.1 | Watch channels every 3 h and auto-clip new GTA VI VODs | ✅ (fill in `config/watchlist.yaml`) |
| 3.2 | **Scheduled publishing**: upload private with `publishAt` spread over your best hours | 🔧 |
| 3.3 | **Notifications** (Discord/Telegram) when clips are ready to review | 🔧 |
| 3.4 | **Review queue**: approve/reject clips in the web app, and only approved ones upload | 🔧 |
| 3.5 | **Duplicate guard**: skip moments already clipped from another streamer's POV | 🔧 |

Steps for 3.1 right now:
1. Add channels to `config/watchlist.yaml` (each must also be in `streamers.yaml`).
2. Repo → **Settings → Secrets and variables → Actions → Variables**: add `AUTO_UPLOAD` = `true` if you want automatic uploads, which stay private by default.
3. Actions → **Watch channels** → **Run workflow** with *dry_run* ticked, to check it finds the right VODs.

**Done when:** a week passes where you only review and approve clips.

---

## Phase 4: Scale (month 2–3)

**Goal:** more output, more platforms, smarter picks.

1. **24/7 free server:** an Oracle Cloud *Always Free* VM (4 ARM cores, 24 GB RAM) running the Docker app, plus a Cloudflare Tunnel, with `CLIPPER_PASSWORD` set. Steps are in the README under *Free 24/7 hosting*.
2. **More quota:** request a YouTube API quota increase (a free form in Google Cloud) once the channel is established.
3. 🔧 **TikTok / Instagram Reels export:** the same vertical clips with platform-specific captions. Auto-posting needs those platforms' app review, so start with export plus manual posting.
4. 🔧 **Learning loop:** pull each clip's views via the YouTube Analytics API and auto-tune `detection.weights` toward what actually gets views.
5. 🔧 **Multi-channel:** separate YouTube tokens per channel (e.g. one channel per big streamer).
6. **GPU:** on a PC with an NVIDIA GPU, set `WHISPER_DEVICE=cuda` for about 10× faster captions.

---

## Rules that keep the channel alive (read once)

- **Permission + credit, always.** The allowlist, the on-video credit and the description link are built in. Don't turn them off.
- **Copyright claims happen.** If a streamer asks you to stop, remove their clips and take them off the allowlist the same day.
- **Monetization:** YouTube's *reused content* policy can block monetization of plain reuploads. Edits, captions, hooks and commentary help. Phase 2 features make clips more transformative.
- **Music:** streams often have copyrighted background music, which leads to Content ID claims. Prefer moments where the streamer is talking. A 🔧 "mute background music" option (source separation) can be added if this becomes common.
- **Quota:** about 6 uploads/day. The app enforces the cap, so going over just waits.

---

## How to ask Claude for the next step

Copy one of these into a new session with this repo:

- "Add per-streamer presets (roadmap 2.3) to Clipperpro."
- "Build the trim editor and re-render button (2.4 + 2.5)."
- "Add a hook title overlay for the first 2 seconds (2.6)."
- "Add scheduled publishing with publishAt (3.2)."
- "Add Discord notifications when a job finishes (3.3)."
- "This VOD failed: `<paste the error>`. Fix it."
