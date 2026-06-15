# automate-yt — Documentation

A fully automated, zero-cost pipeline that generates and publishes YouTube Shorts end-to-end: trend research → script → voice → transcription → video render → upload → analytics.

This `docs/` folder is the entry point for anyone joining the project. Start with **Getting Started**, then read **Architecture** to understand the moving parts.

## Reading order

| # | Doc | What it covers |
|---|---|---|
| 1 | [Getting Started](getting-started.md) | Clone, install, configure, first pipeline run |
| 2 | [Architecture](architecture.md) | Modules, data flow, how the pieces connect |
| 3 | [Pipeline Reference](pipeline.md) | Each of the 6 pipeline stages in depth |
| 4 | [Configuration Reference](configuration.md) | Every env var, what it controls, defaults |
| 5 | [YouTube OAuth Setup](youtube-oauth-setup.md) | Google Cloud + `client_secrets.json` walkthrough |
| 6 | [Troubleshooting](troubleshooting.md) | Common errors and fixes |
| 7 | [Growth Playbook](growth-playbook.md) | Algorithm levers — what only humans can do |

## Project at a glance

- **Language:** Python 3.11+
- **Cost:** $0 — uses free tiers (Gemini, Pexels, Pixabay) + local models (Whisper, edge-tts)
- **Output:** 1080×1920 (9:16) MP4 Shorts, 50–60 seconds each
- **Database:** MongoDB Atlas (free tier) — tracks pipeline state per video
- **Entry point:** `python -m src.orchestrator` (or `python run_ui.py` for the web UI)

## Quick map

```
src/
├── orchestrator.py        ← ties everything together
├── trend/                 ← Google Trends research
├── script/                ← Gemini → JSON script (hook, body, CTA, comment_bait)
├── audio/                 ← edge-tts → MP3, LUFS-normalized
├── transcribe/            ← faster-whisper → word-level timestamps
├── video/                 ← moviepy + Pexels → final MP4 + thumbnail
├── uploader/              ← YouTube Data API v3
├── analytics/             ← YouTube Analytics feedback loop
├── db/                    ← MongoDB schema + repository
└── web/                   ← Flask UI for running pipelines from a browser
```
