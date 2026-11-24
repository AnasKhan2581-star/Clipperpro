# Instagram Reels upload: one-time setup (about 20 minutes)

After this, every reel in the app gets a working **Instagram ▶** button that publishes it as a
public Reel on your clip account. It uses Meta's official API, is free, and nothing leaves your PC
except the upload to Instagram itself.

**No setup at all?** You can still post by hand: click **Download** on a reel, then **Copy caption**,
and post it from the Instagram app or instagram.com (both can post Reels).

Why the Facebook Page step: Meta's API only lets you upload a video file from your own computer
through its "Facebook Login" route, and that route needs the Instagram account linked to a
Facebook Page. The page can be empty and doesn't need to be promoted.

---

## Step 1: Make the clip account a professional account
In the Instagram app on the clip account (for example TomoClips):
**Profile → ☰ → Settings and activity → Account type and tools → Switch to professional account**,
pick a category like *Gaming video creator*, then choose **Creator**. It's free and you can switch back.

## Step 2: Link it to a Facebook Page
1. On Facebook (your own login is fine), create a Page: **Menu → Pages → Create new Page**, named for example
   *TomoClips*. You can skip all the optional steps.
2. Link the Instagram account: on the Page, open **Settings → Linked accounts → Instagram → Connect account**,
   and log in to the clip account.
   (Or in Meta Business Suite: **Settings → Instagram accounts → Connect**.)

## Step 3: Create a Meta developer app
1. Go to https://developers.facebook.com/, log in with the same Facebook account, and click **Get started** if asked.
2. **My Apps → Create app**. When asked what the app should do, pick **Other**; for the app type pick **Business**.
   Name it `clipper`.
3. In the app dashboard, add the **Instagram** product and choose **API setup with Facebook login**.
   If it asks for **Facebook Login for Business**, add that too.
4. Open **App settings → Basic**. Copy the **App ID** and the **App secret** (click **Show**). Keep the
   secret private.

The app can stay in **Development** mode: you're its admin, so it works for your own accounts without
App Review.

## Step 4: Get a token
1. Open https://developers.facebook.com/tools/explorer/
2. On the right, pick your `clipper` app under **Meta App**, and **User Token** under **User or Page**.
3. Under **Permissions**, add all five:
   `instagram_basic`, `instagram_content_publish`, `pages_show_list`, `pages_read_engagement`, `business_management`
4. Click **Generate Access Token**, log in, and when asked, tick the Page *and* the Instagram account from
   Step 2. Then **Allow**.
5. Copy the long token from the **Access Token** box.

This token only lasts about an hour, but the app swaps it for a permanent one right away (Step 5).

## Step 5: Connect it in the app
Open http://localhost:8000 → **Streamers & settings** → **Instagram**. Paste the **App ID**, **App secret**
and **Access token**, then click **Connect Instagram**. It should say *Connected as @yourclipaccount*.

Done. The values are saved only on this PC, in `data/instagram.json` (never committed to GitHub).

---

## Using it
- **Instagram ▶** on a reel asks you to confirm, then uploads, waits for Instagram to process it (usually
  under a minute), and publishes it. The card shows *✔ Live on Instagram ↗* with the link.
- The caption is the clip's **description**: edit it first (hook, @Tomovids, hashtags). Instagram allows
  2,200 characters and 30 hashtags; extras are trimmed.
- It posts **immediately and publicly**. For the Tomo campaign, submit the link in Content Rewards within 30 minutes.
- Limit: 25 Reels a day by default (`instagram.daily_upload_cap` in `config/settings.yaml`). Instagram itself allows 50.

## If something goes wrong
| You see | Do this |
|---|---|
| "None of your Facebook Pages has an Instagram professional account linked" | Redo Step 1 and Step 2, then generate a new token (Step 4) and connect again. When generating the token, tick the Page and the Instagram account. |
| "a permission is missing" | Generate a new token with **all five** permissions (Step 4), then connect again. |
| "the token expired or was revoked" | You changed your Facebook password or removed the app. Do Step 4 and Step 5 again. |
| "Invalid client_secret" / "Error validating application" | Re-copy the App ID and App secret from **App settings → Basic**. |
| "Instagram couldn't process the video" | Try **Re-cut clip** once (renders a fresh file), then upload again. |
