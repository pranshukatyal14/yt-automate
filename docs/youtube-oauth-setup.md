# YouTube OAuth Setup

The single most error-prone part of onboarding. Follow these steps once and you'll never touch it again.

## What you need

- A Google account that owns the YouTube channel you want to publish to
- 10 minutes
- The end state: a file named `client_secrets.json` at the **project root** (same directory as `requirements.txt`)

## Step 1 — Create a Google Cloud project

1. Go to https://console.cloud.google.com/
2. Top bar → project dropdown → **New Project**
3. Name it (e.g. `automate-yt`) → **Create**
4. Wait ~10 seconds, then select the project from the dropdown.

## Step 2 — Enable the YouTube Data API v3

1. Sidebar → **APIs & Services → Library**
2. Search **YouTube Data API v3** → click → **Enable**
3. (Optional, only if you'll use `src/analytics/youtube_analytics.py`) also enable **YouTube Analytics API**.

## Step 3 — Configure the OAuth consent screen

1. Sidebar → **APIs & Services → OAuth consent screen**
2. User type: **External** → **Create**
3. Fill in:
   - **App name:** `automate-yt` (anything works)
   - **User support email:** your email
   - **Developer contact:** your email
4. **Save and Continue**.
5. **Scopes** step: click **Add or Remove Scopes**, search and select:
   - `https://www.googleapis.com/auth/youtube.force-ssl`

   That's the only scope this project needs. It's a strict superset of `youtube.upload` and `youtube.readonly`.

   **Save and Continue**.
6. **Test users** step: click **Add Users**, add the Google account that owns your channel. **Save and Continue**.

   > As long as the app stays in **Testing** mode, only listed test users can authorize. You don't need to submit for verification — you'll just see a "Google hasn't verified this app" warning during consent, which you bypass with **Advanced → Go to {app} (unsafe)**. That's normal and expected.

## Step 4 — Create the OAuth client

1. Sidebar → **APIs & Services → Credentials**
2. **+ Create Credentials → OAuth client ID**
3. **Application type: Desktop app** ← important. Not Web, not iOS.
4. Name: `automate-yt-desktop` (anything)
5. **Create** → a dialog shows your client ID + secret. You can close it.
6. In the credentials list, find the new entry under **OAuth 2.0 Client IDs**. Click the **download icon (⬇)** at the right.
7. A JSON file downloads with a name like `client_secret_<long-id>.apps.googleusercontent.com.json`.

## Step 5 — Place the file

```bash
mv ~/Downloads/client_secret_*.json "<project-root>/client_secrets.json"
```

Verify:
```bash
ls -la <project-root>/client_secrets.json
```
Should exist at the same level as `requirements.txt`.

> **Don't** put it inside `src/` — the uploader reads it from the current working directory, which is the project root when you run `python -m src.orchestrator`.

## Step 6 — First run, OAuth dance

```bash
python -m src.orchestrator --topic "test run"
```

When the pipeline reaches step 5/6 (upload):
1. Logs: `Starting OAuth flow — browser will open for consent…`
2. Default browser opens at `accounts.google.com`
3. Pick the Google account you added as a test user
4. **"Google hasn't verified this app"** warning:
   - Click **Advanced** (bottom-left)
   - Click **Go to {app name} (unsafe)** link
5. Approve the `youtube.force-ssl` scope
6. Browser shows "The authentication flow has completed. You may close this window."
7. Back in the terminal, the upload proceeds.

A `token.json` file is now saved at the project root. **Do not commit it to git** — `.gitignore` should already cover it; double-check.

## Step 7 — Subsequent runs

`token.json` is reused silently. When the access token expires (~1 hour), it's refreshed automatically via the long-lived refresh token. No browser opens again.

## When OAuth re-prompts you

The flow restarts (browser opens) in these cases:

| Trigger | Why |
|---|---|
| `token.json` deleted | No token to load |
| Scope changes in `SCOPES` constant | Saved scopes no longer match required scopes; uploader auto-deletes the token and re-runs flow |
| Refresh token revoked in Google Account → Security → Third-party access | Manual revocation |
| Google Cloud project deleted or OAuth client deleted | Refresh fails permanently |

## Common errors

**`FileNotFoundError: client_secrets.json`**
→ File not at project root. See Step 5.

**`invalid_scope: Bad Request` during refresh**
→ Saved token has scopes the OAuth client no longer allows. Fix: delete `token.json`, run again. The uploader's auto-detect should catch this in future runs.

**`access_denied` after clicking Continue**
→ Your Google account isn't in the OAuth consent screen's test-user list. Add it (Step 3.6).

**`The OAuth client was not found`**
→ You're pointing at the wrong `client_secrets.json` (maybe from a deleted GCP project). Re-download from Step 4.

**`quotaExceeded` on upload**
→ The default YouTube Data API quota is **10,000 units/day**. Each upload costs ~1,600 units → ~6 uploads/day max on default quota. Request a quota increase in GCP if you need more.

## Quota math (memorize)

| Action | Cost |
|---|---|
| Upload video | 1,600 units |
| Set thumbnail | 50 units |
| Insert comment | 50 units |
| Get video metadata | 1 unit |

Default daily quota = **10,000 units** → roughly **6 full upload cycles/day** out of the box. Once you're consistently hitting that ceiling, file a quota increase request — Google usually approves modest bumps within a week.
