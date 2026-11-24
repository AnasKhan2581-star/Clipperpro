# Getting started: the easy way

Pick **one** of the two ways below. You don't need to know how to code for either.

| | Way A: GitHub (no install) | Way B: Your computer (Docker) |
|---|---|---|
| Cost | Free | Free |
| Setup time | 5 minutes | 15 minutes |
| Best for | Twitch/Kick VODs, trying it out | YouTube VODs, daily use, the nice web app |
| Needs your PC on? | No | Yes, while it works |

---

## Way A: Run it on GitHub (no install)

### Step 1: Add the streamer to your allowlist (one time per streamer)
1. Open your repo on GitHub, go to the `config` folder and click `streamers.yaml`.
2. Click the ✏️ **pencil** (Edit) icon.
3. Replace the line `streamers: []` with this, using the streamer's real names:
   ```yaml
   streamers:
     - name: StreamerName
       match: [streamername, "twitch.tv/streamername"]
       credit_url: https://www.twitch.tv/streamername
       permission: "Clip policy on their stream panel, checked 2026-11-18"
   ```
   Spaces matter: keep the same indentation as above.
4. Click **Commit changes** → **Commit changes**.

> Only add streamers who allow clip channels. Check their channel's About/panels or ask them, and write in `permission` where you saw it.

### Step 2: Make clips
1. Click the **Actions** tab at the top of your repo.
2. On the left, click **Clip a VOD**.
3. Click the grey **Run workflow** button on the right.
4. Paste the VOD link, for example `https://www.twitch.tv/videos/1234567890`.
5. Choose:
   - **clips**: how many (5 is good)
   - **layout**: `facecam` if the streamer has a webcam, otherwise `blur`
   - **facecam_box**: where the webcam is. The default is bottom-left. For bottom-right use `0.72,0.62,0.28,0.38`.
6. Click the green **Run workflow** button.

### Step 3: Download your clips
1. Wait about 5–20 minutes. Refresh the page and the run gets a ✅.
2. Click the run, scroll down to **Artifacts** and click **clips** to download a zip.
3. Inside are `clip_01.mp4`, `clip_02.mp4`… plus a `.json` file per clip with a ready title and description.

### Step 4: Post
- **Manually:** in YouTube Studio, choose **Create → Upload** and paste the title and description from the `.json` file.
- **Automatically:** do [YouTube auto-upload](#youtube-auto-upload-one-time-setup) once, then tick **upload** in Step 2.

> If a YouTube link fails with *"Sign in to confirm you're not a bot"*, YouTube is blocking GitHub's servers. Use Way B for YouTube links, or add the `YTDLP_COOKIES` secret (see the README). Twitch and Kick links work fine on GitHub.

---

## Way B: Run the app on your computer

### Step 1: Install Docker Desktop (one time)
Download it from https://www.docker.com/products/docker-desktop/, install it and open it. Wait until it says **Engine running**.

### Step 2: Get the app
- On your repo page, click the green **Code** button, then **Download ZIP**, and unzip it, **or**
- if you have git, run `git clone https://github.com/AnasKhan2581-star/Clipperpro.git`.

### Step 3: Start it
Open a terminal in the unzipped folder (Windows: right-click the folder → **Open in Terminal**; Mac: drag the folder onto Terminal) and run:
```bash
docker compose up -d --build
```
The first time takes about 5 minutes. After that it starts in seconds.

### Step 4: Use it
1. Open **http://localhost:8000** in your browser.
2. Click **Streamers & settings**, add the streamer (name, their channel link, where their clip permission comes from) and click **Save**.
3. Paste a VOD link, pick a layout and click **Find highlights & render**.
4. Watch the progress bar. When it's done, preview each clip, edit the title and click **Download** or **Upload ▶**.
5. For Instagram: click **Instagram ▶** (after the one-time [Instagram setup](INSTAGRAM_SETUP.md)), or
   **Download** + **Copy caption** and post it from your phone.
6. Clip too short or starts too late? Open **✂ Trim / extend** on the clip, set the start and end (or pause the video and
   click **Start/End at playhead**), then **Re-cut clip**. The new version appears in about a minute.

**Stream still going?** Paste the live stream's link, tick **🔴 Clip while live** and click **Start live clipping**.
New clips show up about every 20 minutes (open the LIVE job to see its chunks). Press **■ Stop** when you're done,
or just let it run: it stops by itself when the stream ends. Keep the app and your PC on while it runs.

To stop it, run `docker compose down`. To update it later, run `git pull` (or download the ZIP again) and then `docker compose up -d --build`.

---

## YouTube auto-upload (one-time setup)

This takes about 15 minutes, once. It's free.

1. Go to https://console.cloud.google.com/ and sign in with the Google account that owns your YouTube channel.
2. Click the project picker at the top, then **New project**. Name it `clipper` and click **Create**.
3. Search for **YouTube Data API v3**, open it and click **Enable**.
4. In the left menu, open **OAuth consent screen**:
   - User type: **External**, then **Create**
   - Fill in the app name (`clipper`) and your email, then click **Save** until you're done
   - Under **Test users**, add your own Gmail
   - Click **Publish app** and confirm. Without this, your login expires every 7 days.
5. In the left menu, open **Credentials**, then **+ Create credentials → OAuth client ID**. For application type choose **Desktop app** and click **Create**, then **Download JSON**.
6. Rename the downloaded file to `client_secret.json` and put it in the app's `data` folder.
7. Run, in the app folder:
   ```bash
   docker compose run --rm --service-ports clipper auth-youtube
   ```
   Open the link it prints, choose your channel and click **Allow**. If Google warns *"Google hasn't verified this app"*, click **Advanced → Go to clipper**. That's expected because it's your own app.
8. Done. `data/youtube_token.json` now exists and the **Upload ▶** buttons work.

**For GitHub (Way A) too:** go to your repo → **Settings → Secrets and variables → Actions → New repository secret** and add:
- `YT_CLIENT_SECRET_JSON`: paste the whole contents of `client_secret.json`
- `YT_TOKEN_JSON`: paste the whole contents of `data/youtube_token.json`

Limits: about **6 uploads per day** on the free quota. Uploads are **private** by default, so you can check each one in YouTube Studio before making it public.

---

## Your daily routine (about 10 minutes)

1. A streamer you follow finishes a GTA VI stream, and their VOD appears.
2. Paste the link (Way A or B) and wait.
3. Watch the clips. Delete the boring ones and fix the titles.
4. Upload your best 3–6, as private at first, and schedule them in YouTube Studio for your audience's peak hours.
5. Once a week, check YouTube Studio → Analytics for which clips got views and do more of that. See [ROADMAP.md](ROADMAP.md), Phase 1.

## When something goes wrong

| You see | Do this |
|---|---|
| "not in your streamer allowlist" | Do Step 1 again. Check the spelling of `match` against the channel name in the link. |
| "Sign in to confirm you're not a bot" | Use Way B for YouTube links, or add cookies (see the README). |
| "Stream is still live" | Tick **🔴 Clip while live** (Way B), or wait until the stream ends and the VOD is ready. |
| Facecam crop shows the wrong area | Use the corner presets in the app, or change `facecam_box`. |
| Captions are a bit off | Choose the Whisper model `small` (slower, more accurate). |
| "Daily upload cap reached" | Wait 24 hours. That's YouTube's free limit. |
| GitHub run shows ❌ | Click it, then click the red step to read the error. The message says what to fix. |
