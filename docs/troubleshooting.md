# Troubleshooting

Sorted by likelihood. Search for the error string.

## Setup / Installation

### `ModuleNotFoundError: No module named 'src'`
You're running a script from inside `src/`. Always run from the project root:
```bash
python -m src.orchestrator    # ✓
python src/orchestrator.py    # ✗
```

### `ffmpeg: command not found` during render or LUFS normalization
`imageio[ffmpeg]` ships an ffmpeg binary, but it's not on PATH. Either:
- Install system ffmpeg: `brew install ffmpeg` (macOS) / `apt install ffmpeg` (Linux), or
- Just ignore — LUFS normalization will skip and log a warning; the rest works.

### `pydub` ImportError or `Couldn't find ffmpeg or avconv`
Same as above — system ffmpeg solves it.

## Scriptwriter (Gemini / Groq)

### `503 Service Unavailable` from Gemini
Cascade kicks in automatically (2.5-flash → 2.0-flash → 2.5-pro → Groq). If you don't have `GROQ_API_KEY` set, the run fails. Fix: get a free Groq key at console.groq.com and set it in `.env`.

### `ValueError: LLM returned non-JSON output`
Rare — Gemini occasionally returns markdown-wrapped JSON. Cascade retries with the next model. If it persists, lower `temperature` in `_call_gemini_model` (default 0.85).

### `Script JSON missing required keys: {'comment_bait'}` (or similar)
LLM didn't follow the schema. Auto-retries via the cascade. If consistent, the prompt in `src/script/scriptwriter.py` may need a stricter restatement.

## Voice (edge-tts)

### `edge_tts.exceptions.NoAudioReceived`
Microsoft Edge TTS service hiccup. Retry the run. If persistent, try a different voice via `TTS_VOICE` env var.

### Voice sounds rushed or unintelligible
`TTS_RATE` is too high. Default is `+25%` — drop to `+15%` or `+20%` in `.env`:
```
TTS_RATE=+15%
```

### Audio is too quiet vs other Shorts in the feed
LUFS normalization should handle this (target -14 LUFS). If skipped, you'll see `ffmpeg not on PATH — skipping LUFS normalization` — install ffmpeg.

## Transcription (faster-whisper)

### First run downloads model (~150MB for base) silently
Normal. Subsequent runs reuse the cached model.

### `RuntimeError: CUDA out of memory`
Set `WHISPER_DEVICE=cpu` in `.env`, or use a smaller `WHISPER_MODEL_SIZE`.

### Word timestamps look misaligned
- Check `WHISPER_MODEL_SIZE` — `tiny` is fast but inaccurate. Use `base` or `small`.
- Make sure the audio language matches `--lang` — if you pass `--lang hi` but the voice spoke English, alignment will be bad.

## Video render (moviepy / Pexels)

### `No clips fetched — using black fallback`
Pexels + Pixabay both returned zero results for all 6 keywords. Causes:
1. Both API keys invalid → check `.env`
2. Keywords too abstract (Gemini occasionally produces non-visual phrases) → re-run
3. Network blocked

### Render is very slow (>5 min for 55s video)
- Mostly CPU-bound. Expected on older Macs / single-core machines.
- `WHISPER_DEVICE=cuda` (if you have GPU) speeds up transcription.
- Video render itself can't easily be GPU-accelerated in the current moviepy stack.

### Captions show as boxes (`▯▯▯`)
Font doesn't support the script's language. Check `_FONT_MAP` in `src/video/video_editor.py` — add a language entry pointing at a font that supports the script (e.g. for Hindi: a Devanagari-supporting TTF).

### Hook card text is cut off
The hook is >15 words despite the prompt rule. Re-run — Gemini occasionally over-shoots. If consistent, tighten the prompt's hook length constraint.

## YouTube upload

### `FileNotFoundError: client_secrets.json`
File not at project root. See [YouTube OAuth Setup, Step 5](youtube-oauth-setup.md#step-5--place-the-file).

### `invalid_scope: Bad Request` during refresh
Saved token has scopes that no longer match. Fix:
```bash
rm token.json
python -m src.orchestrator --topic "..."
```
The next run will re-prompt for consent. If `yt-analytics.readonly` was previously requested but the YouTube Analytics API isn't enabled in your GCP project, enable the API or remove the scope from `SCOPES` in `src/uploader/youtube_uploader.py`.

### `Google hasn't verified this app` browser warning
Expected for unverified personal apps. Click **Advanced → Go to {app} (unsafe)**. See [OAuth Setup, Step 6](youtube-oauth-setup.md#step-6--first-run-oauth-dance).

### `quotaExceeded` HTTP 403
You've used 10,000 units today. Each upload = ~1,600 units → ~6 uploads/day. Either:
- Wait until UTC midnight for the quota to reset, or
- Request a quota increase in Google Cloud Console.

### `Thumbnail upload failed … channel not verified for custom thumbnails`
Soft warning, not an error — the video uploads fine without a custom thumbnail. To fix, verify your YouTube channel: youtube.com/verify (requires phone number).

### Auto-comment fails with `commentsDisabled`
Channel has comments off globally, or the specific video has them off. Check Studio → Content → Comment settings.

### Scheduled upload didn't publish at the scheduled time
- Check `publishAt` was passed as RFC 3339 UTC: `2026-04-24T20:00:00.000Z` (not local time).
- Check the video is `Private + Scheduled` in Studio. If it's just `Private`, the scheduling failed silently.

## MongoDB

### `pymongo.errors.ServerSelectionTimeoutError`
- `MONGO_URI` wrong, or
- IP not whitelisted in Atlas → add your current IP in Atlas → Network Access, or
- Local MongoDB not running.

### Document status stuck on `EDITING` or `UPLOADING`
The pipeline crashed mid-stage and exception handling didn't update the status. Check `logs/pipeline.log` for the traceback. The doc is stale — you can ignore it or delete it manually.

## Web UI (Flask)

### `Address already in use` on port 5001
Another Flask instance is still running. Find and kill it:
```bash
lsof -ti:5001 | xargs kill
```

### Status polling shows `404`
The `run_id` doesn't exist (run failed before insert, or DB lost the doc). Refresh the page.

## General

### "Where do I find the full log?"
`logs/pipeline.log` — appended forever, structured with timestamps + module + level. Grep for `doc_id=<id>` to follow a single run.

### "How do I retry a failed run?"
Just re-run with the same topic:
```bash
python -m src.orchestrator --topic "<same topic>"
```
Each run gets a fresh `doc_id` + `run_id`. Old failed docs stay in DB.

### "How do I purge everything and start over?"
```bash
rm -rf output/ logs/ token.json
# Optionally drop the MongoDB collection in Atlas UI
```
Don't delete `client_secrets.json` unless you also want to re-do OAuth setup.
