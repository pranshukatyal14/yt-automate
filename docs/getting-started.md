# Getting Started

This walks you from zero to a published YouTube Short.

## 1. Prerequisites

- **Python 3.11+** (3.9 works but 3.11+ is the supported target)
- **ffmpeg** on `PATH` — required for video render and LUFS audio normalization. Installed automatically via `imageio[ffmpeg]`, but a system install is also fine (`brew install ffmpeg`).
- **MongoDB Atlas** free cluster (or any MongoDB instance)
- **Google account** with access to Google Cloud Console + a YouTube channel
- **macOS/Linux** — Windows works but font paths in `src/video/video_editor.py` will need adjustment.

## 2. Clone and install

```bash
cd "path/to/projects"
git clone <repo-url> automate-yt
cd automate-yt

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 3. Get API keys (all free)

| Service | Used for | Where |
|---|---|---|
| **Gemini** | Scriptwriting | https://aistudio.google.com/apikey |
| **Groq** (optional) | Scriptwriting fallback when Gemini is rate-limited | https://console.groq.com/ |
| **MongoDB Atlas** | Pipeline state DB | https://www.mongodb.com/cloud/atlas |
| **Pexels** | Stock video footage | https://www.pexels.com/api/ |
| **Pixabay** (optional) | Stock footage fallback | https://pixabay.com/api/docs/ |
| **YouTube Data API v3** | Upload + thumbnail + comments | See [YouTube OAuth Setup](youtube-oauth-setup.md) |

## 4. Configure `.env`

Copy the template and fill in your keys:

```bash
cp .env.example .env
```

At minimum you must set: `GEMINI_API_KEY`, `MONGO_URI`, `PEXELS_API_KEY`. See the [Configuration Reference](configuration.md) for every option.

## 5. Set up YouTube OAuth

This is the trickiest step. Follow the dedicated guide: [YouTube OAuth Setup](youtube-oauth-setup.md).

End state: `client_secrets.json` exists at the **project root** (not inside `src/`).

## 6. First run

### Option A — CLI, manual topic

```bash
python -m src.orchestrator --topic "5 iPhone tricks Apple hides from you"
```

### Option B — CLI, auto-trending

```bash
python -m src.orchestrator
```
Researches Google Trends and picks the topic itself.

### Option C — Web UI

```bash
python run_ui.py
```
Opens a Flask app at `http://localhost:5001`. Submit topics from the browser; watch live status updates.

### Option D — Render without uploading

```bash
python -m src.orchestrator --topic "..." --no-upload
```
Useful while iterating — output lands in `output/video/<run_id>_final.mp4`.

## 7. What happens on first upload

The first time the uploader runs, a browser opens for Google OAuth consent. Click **Advanced → Go to {app name} (unsafe)** (this is normal for an unverified internal-use app), then approve the `youtube.force-ssl` scope.

A `token.json` file is saved at the project root. Subsequent uploads reuse it silently — no browser prompts.

## 8. Verify it worked

After a successful run you'll see:

```
[5/6] Uploaded! https://youtu.be/<video_id>
[5a] Custom thumbnail set on <video_id>
[5b] Bait comment posted — pin manually in Studio: https://studio.youtube.com/...
```

Open the video URL — it should be live (or scheduled, if you used `--schedule`).

## 9. Schedule recurring uploads

```bash
python -m src.orchestrator --schedule "20:00"           # publish today at 20:00 UTC
python -m src.orchestrator --schedule "2026-05-15 14:30" # specific UTC datetime
```

The MP4 is uploaded as private; YouTube auto-publishes it at the scheduled time.

## 10. Set up daily auto-posting

The algorithm rewards consistent daily uploads at a fixed time slot. The daily runner automates this completely — one video per day, idempotency guard so it never double-posts.

### Step 1 — Set your post time in `.env`

```bash
POST_TIME=20:00     # 24h format — pick one slot and hold it 30 days
POST_TZ=+5.5        # your UTC offset (IST=+5.5, EST=-5, PST=-8, UTC=0)
```

### Step 2 — Test it manually first

```bash
# Dry run — renders a video locally, no upload
python scripts/daily_run.py --dry-run

# Live run — produces and publishes one niche-locked Short
python scripts/daily_run.py
```

Running it twice on the same day? The second call exits cleanly with "Already produced today." Use `--force` to override.

### Step 3 — Install the launchd job (macOS)

```bash
cp "scripts/com.automateyt.daily.plist" ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.automateyt.daily.plist
```

That's it — your Mac will fire `daily_run.py` every day at `POST_TIME`. Logs go to `logs/daily.log` and `logs/daily_error.log`.

**To uninstall:**
```bash
launchctl unload ~/Library/LaunchAgents/com.automateyt.daily.plist
rm ~/Library/LaunchAgents/com.automateyt.daily.plist
```

### Step 3 (Linux / server — cron alternative)

```bash
# Edit crontab:  crontab -e
# Run at 20:00 UTC every day (adjust time to your slot):
0 20 * * * cd "/path/to/automate-yt" && .venv/bin/python scripts/daily_run.py >> logs/daily.log 2>&1
```

### Important caveat — laptop sleep

`launchd` fires only when your Mac is awake. If your laptop sleeps at the trigger time, the job is skipped for that day. For reliable unattended posting, run on an always-on machine (a cheap cloud VM or a Mac mini left on).

### Weekly analytics refresh

Run this every Sunday to feed the analytics→scriptwriter feedback loop:

```bash
python -m src.analytics.youtube_analytics --refresh-all
```

The next video generated after this will be primed with your channel's real winners.

## 11. Next steps

- Read [Architecture](architecture.md) to understand how the modules connect.
- Read the [Growth Playbook](growth-playbook.md) — the algorithm levers you can't fix in code.
- After 20+ videos, run `--refresh-all` weekly so the feedback loop starts compounding.
