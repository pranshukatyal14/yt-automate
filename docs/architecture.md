# Architecture

## High-level data flow

```
                ┌─────────────────────────────────────────────────────┐
                │              src/orchestrator.py                    │
                │              run_pipeline()                         │
                └─────────────────────────────────────────────────────┘
                                       │
        ┌───────────┬───────────┬──────┴───────┬───────────┬───────────┐
        ▼           ▼           ▼              ▼           ▼           ▼
    trend       script       audio        transcribe    video       uploader
   (optional)
        │           │           │              │           │           │
        ▼           ▼           ▼              ▼           ▼           ▼
  Google Trends  Gemini      edge-tts    faster-whisper  moviepy    YouTube
   pytrends     2.5 Flash    Microsoft     local Whisper  ffmpeg    Data API
                Groq llama   neural TTS    word-level     Pexels    OAuth2
                 (fallback)                 timestamps    Pixabay
                                                          PIL captions
                                                          Ken Burns

       Every stage writes status + artifacts to MongoDB (src/db/models.py)
```

## Modules

### `src/orchestrator.py`
The conductor. `run_pipeline(topic, upload, style, lang, publish_at)` walks the 6 stages, writes progress to MongoDB, cleans up temp files, and returns a result dict (doc_id, youtube_id, paths).

### `src/trend/trend_researcher.py`
Runs only when no `--topic` is provided. Scrapes Google Trends with `pytrends`, ranks topics with Gemini, picks a winner + recommends a `style` (`factual` or `story`). Respects `CHANNEL_NICHE` so trend research stays inside your lane.

### `src/script/scriptwriter.py`
`ScriptwriterService.write_script(topic, style, lang)` returns structured JSON:

```json
{
  "hook":            "≤15 word pattern-interrupt opener",
  "body":            "80–100 words of natural speech",
  "call_to_action":  "loops back into hook seamlessly",
  "comment_bait":    "one-word-answer question",
  "visual_keywords": ["6 concrete camera-shot phrases"],
  "title":           "≤92 chars, keyword front-loaded",
  "description":     "first line is a hook for the 'more' tap",
  "tags":            ["12 mixed broad+specific tags"]
}
```

Cascade strategy: tries `gemini-2.5-flash` → `gemini-2.0-flash` → `gemini-2.5-pro` on 503/429, then falls back to Groq `llama-3.3-70b-versatile`.

Style profiles live as two system prompts: `_SYSTEM_PROMPT_FACTUAL` (energy, lists-as-conversation) and `_SYSTEM_PROMPT_STORY` (3-act, present tense, in media res).

### `src/audio/voice_service.py`
`VoiceService.synthesise(text, filename)` produces a final MP3:
1. Microsoft Edge neural TTS via `edge-tts` (no API key, voice mapped per language)
2. pydub: peak normalize + soft dynamic compression
3. Fade in/out
4. **ffmpeg `loudnorm` → -14 LUFS** (matches YouTube's playback normalizer)

Voiceover is delivered clean (no background music) by design.

### `src/transcribe/transcription_service.py`
Runs `faster-whisper` locally (CPU by default). Returns word-level `{word, start, end}` timestamps. `group_into_captions(words, words_per_caption=4)` chunks them for karaoke-style captions.

### `src/video/video_editor.py`
`VideoEditorService.render(audio_path, visual_keywords, captions, ...)` is the heavy lifting:
- `PexelsFetcher` + `PixabayFetcher` download portrait HD clips per keyword
- `_render_hook_card` overlays a 1-second bold typography card on the spoken hook (prevents the "stock-footage opening" swipe-away)
- `_build_background` enforces ≤2.2s/clip cadence by cycling and shuffling the clip pool — clips never linger
- `_apply_ken_burns` slow zoom + color grade on each clip
- `CaptionRenderer.make_word_clips` renders per-word PIL frames (active word highlighted), language-aware fonts
- Composites everything → 1080×1920 @ 30fps MP4

### `src/video/thumbnail.py`
`generate_thumbnail(title, output_path, background_image)` makes a 1280×720 JPEG from a video frame + the script title using PIL. `extract_frame(video, timestamp)` pulls a frame at a given second.

### `src/uploader/youtube_uploader.py`
`YouTubeUploader()` handles:
- OAuth flow (reads `client_secrets.json`, caches `token.json`)
- Single scope: `youtube.force-ssl` (covers upload + thumbnail + comment)
- `upload()` — resumable MP4 upload with metadata + `#Shorts` injection + optional `publishAt` for scheduling
- `set_thumbnail()` — soft-fails if channel isn't verified for custom thumbnails
- `post_comment()` — top-level comment from the channel (used for bait comments)

> **Why this scope:** `youtube.force-ssl` is a strict superset of `youtube.upload` and `youtube.readonly`, so we only ever need consent for one thing.

### `src/analytics/youtube_analytics.py`
Feedback loop. Run with `python -m src.analytics.youtube_analytics --refresh-all` after a batch of uploads. Pulls retention curves + CTR per video so you can spot which formats are working.

### `src/db/models.py`
`VideoRepository` is a thin pymongo wrapper. Every video gets one document tracking the journey:

```
PENDING → SCRIPTING → VOICING → TRANSCRIBING → EDITING → UPLOADING → COMPLETED
                                                                   ↘ FAILED
```

Each `set_*` method updates one field and bumps `updated_at`. The orchestrator never reads pipeline state — MongoDB is purely for observability + the web UI status feed.

### `src/web/app.py`
Flask UI. Spawns `run_pipeline()` in a background thread per request and exposes `/api/status/<run_id>` for polling. Useful when you want to fire off a batch from your phone.

## Filesystem outputs

| Path | What | Lifecycle |
|---|---|---|
| `output/audio/<run_id>_voice.mp3` | TTS output | Deleted after video render |
| `output/video/cache/<run_id>/` | Downloaded stock clips | Deleted after render |
| `output/video/<run_id>_final.mp4` | Final MP4 | Deleted after upload |
| `output/video/thumbnails/<run_id>_thumb.jpg` | Custom thumbnail | Kept |
| `token.json` | OAuth credentials | Kept across runs |
| `client_secrets.json` | OAuth client config | Manually placed; kept |
| `logs/pipeline.log` | Full structured log | Appended forever (rotate manually) |

## Why this stack

| Choice | Why |
|---|---|
| Gemini Flash | Cheapest LLM with strong JSON-mode adherence + free tier |
| edge-tts | Best free neural TTS, no rate limits |
| faster-whisper | Local CPU-friendly transcription, no API calls |
| moviepy + ffmpeg | Battle-tested Python video pipeline; ffmpeg comes free with imageio |
| Pexels + Pixabay | Best free portrait stock footage sources |
| MongoDB Atlas | Schema-flexible, free tier handles thousands of docs |
| YouTube Data API v3 | Only official path to programmatic upload |

Every external dep is swappable — the modules talk through narrow function signatures so a paid service (ElevenLabs, RunwayML, etc.) can drop in without changing the orchestrator.
