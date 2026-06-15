# Configuration Reference

Every env var the project reads, what it controls, and sensible defaults. Place them in `.env` at the project root — `python-dotenv` loads it at orchestrator startup.

## Required

| Var | Purpose |
|---|---|
| `GEMINI_API_KEY` | Scriptwriting + trend ranking. Get from https://aistudio.google.com/apikey |
| `MONGO_URI` | Connection string. Example: `mongodb+srv://user:pass@cluster.mongodb.net/` |
| `PEXELS_API_KEY` | Stock footage. Get from https://www.pexels.com/api/ |

## Recommended

| Var | Default | Purpose |
|---|---|---|
| `GROQ_API_KEY` | _(empty)_ | Scriptwriting fallback when all Gemini models return 503/429. Free tier at console.groq.com. Without this, a Gemini outage kills the pipeline. |
| `PIXABAY_API_KEY` | _(empty)_ | Fills gaps when Pexels has <3 portrait clips for a keyword. Free 200 req/min. |
| `CHANNEL_NICHE` | _(empty)_ | Locks the trend researcher to one niche (e.g. `dark psychology`, `iPhone tips`). Single biggest growth lever — see [Growth Playbook](growth-playbook.md). |

## Optional — Models & APIs

| Var | Default | Purpose |
|---|---|---|
| `GEMINI_MODEL` | `gemini-2.5-flash` | Primary script model. Cascade order: this → 2.0-flash → 2.5-pro. |
| `MONGO_DB_NAME` | `automate_yt` | MongoDB database name. |
| `YOUTUBE_CLIENT_SECRETS_FILE` | `client_secrets.json` | Path (relative to CWD) of the OAuth client JSON. |

## Optional — Voice (edge-tts)

| Var | Default | Purpose |
|---|---|---|
| `TTS_VOICE` | _per-lang map_ | Override the neural voice. e.g. `en-US-GuyNeural`. See edge-tts docs for the full list. |
| `TTS_RATE` | `+25%` | Speech speed. `+0%` = normal, `+25%` = high-energy Shorts pace. Going above `+30%` becomes unintelligible. |
| `TTS_LANG` | `en` | Fallback language when `--lang` is not passed and `voice` isn't set explicitly. |

## Optional — Transcription

| Var | Default | Purpose |
|---|---|---|
| `WHISPER_MODEL_SIZE` | `base` | `tiny` (fast, lower quality) → `large` (slow, best). `base` is the sweet spot for English Shorts. |
| `WHISPER_DEVICE` | `cpu` | Set to `cuda` if you have an NVIDIA GPU + CUDA installed → ~5× faster. |

## Optional — Video

| Var | Default | Purpose |
|---|---|---|
| `VIDEO_RESOLUTION_W` | `1080` | Frame width. Don't change unless YouTube changes Shorts spec. |
| `VIDEO_RESOLUTION_H` | `1920` | Frame height (9:16). |
| `VIDEO_FPS` | `30` | Frame rate. 30 is the Shorts standard; 60 doubles render time for no real benefit on phone screens. |

## Optional — Paths

| Var | Default | Purpose |
|---|---|---|
| `OUTPUT_AUDIO_DIR` | `output/audio` | Where voice MP3s land (deleted after render). |
| `OUTPUT_VIDEO_DIR` | `output/video` | Final MP4s + thumbnails + clip cache. |

## Things that look like env vars but aren't

These live in code constants (`src/audio/voice_service.py`, `src/video/video_editor.py`). Edit the files if you need to change them:

| Constant | File | Default | What it does |
|---|---|---|---|
| `TARGET_LUFS` | voice_service.py | `-14.0` | YouTube playback target. Don't change unless YouTube changes their spec. |
| `MAX_SLOT_SEC` | video_editor.py | `2.2` | Maximum seconds per stock clip before forcing a cut. Lower = punchier but harder to source enough variety. |
| `FPS / W / H` constants | video_editor.py | as above | Same as env vars, but the env vars override these at module load. |

## CLI flags

These override env behavior per-run:

| Flag | Effect |
|---|---|
| `--topic "..."` | Skip trend research, use this topic |
| `--no-upload` | Render locally, skip YouTube upload (great for iterating) |
| `--style factual\|story` | Force a style (auto mode normally picks via Gemini) |
| `--lang en\|hi\|es\|fr\|de\|pt\|ja\|ko\|zh\|ar` | Language for script + voice |
| `--schedule "HH:MM"` or `"YYYY-MM-DD HH:MM"` | YouTube auto-publishes at this UTC time |

## Where these are read

Most env vars are read once at module load. Changing `.env` requires restarting the orchestrator process (or the Flask app). The exceptions: `CHANNEL_NICHE` and `--lang`/`--style` are read per-call inside `run_pipeline()`.
