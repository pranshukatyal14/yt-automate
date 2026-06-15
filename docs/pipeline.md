# Pipeline Reference

A run is 6 sequential stages plus optional pre-stage trend research. Each stage updates the MongoDB document and writes log lines you can grep.

## Stage 0 (optional) — Trend Research

**Triggered when:** `--topic` is not provided (or `topic=None` in code).

**What happens:**
1. `TrendResearcher.research()` scrapes Google Trends via `pytrends`.
2. Filters trends by `CHANNEL_NICHE` if set.
3. Asks Gemini to rank candidates for "viral Short potential".
4. Returns `{winner_topic, style, rationale, top3}`.
5. The orchestrator overrides `style` with the researcher's recommendation.

**Output:** `topic` (str), `style` ("factual"|"story"), and rationale logged.

## Stage 1 — Scripting

**Module:** `src/script/scriptwriter.py`

**What happens:**
1. Build a system prompt: `_SYSTEM_PROMPT_FACTUAL` or `_SYSTEM_PROMPT_STORY` depending on `style`.
2. Add language instruction if `lang != "en"`.
3. Try Gemini models in cascade (`2.5-flash` → `2.0-flash` → `2.5-pro`), fall back to Groq `llama-3.3-70b-versatile`.
4. Parse JSON, validate required keys.
5. Save script + metadata to MongoDB.
6. Orchestrator prepends the spoken hook to the description (mobile users see the same line they hear in second 1).

**Output keys:** `hook`, `body`, `call_to_action`, `comment_bait`, `visual_keywords`, `title`, `description`, `tags`.

**Hard constraints (enforced via prompt):**
- Hook ≤15 words, pattern-interrupt, no warm-up
- 130–160 word total target → ~55 sec audio
- Title front-loads keyword in first 40 chars
- AI-related words BANNED in title/description/tags (anti-detection)

## Stage 2 — Voice Synthesis

**Module:** `src/audio/voice_service.py`

**What happens:**
1. `script_to_spoken_text()` concatenates hook + body + CTA + comment_bait with sentence breaks.
2. `edge-tts` synthesises MP3 using the language-mapped voice at `TTS_RATE` (default `+25%`).
3. pydub: peak normalize → soft compression (threshold -20dB, ratio 2.5:1).
4. Fade-in 300ms / fade-out 600ms.
5. **ffmpeg `loudnorm` filter** → -14 LUFS integrated (I=-14, TP=-1.5, LRA=11).
6. Export 192kbps MP3.

> The voiceover ships clean — no background music by design.

**Output:** `output/audio/<run_id>_voice.mp3` (~55 seconds, ~1.3MB).

## Stage 3 — Transcription

**Module:** `src/transcribe/transcription_service.py`

**What happens:**
1. `faster-whisper` (default `base` model on CPU) transcribes the MP3 with word-level timestamps.
2. Returns `[{word, start, end, confidence}, ...]`.
3. `group_into_captions(words, words_per_caption=4)` chunks into 4-word caption groups for karaoke captions.

**Output:** `word_timestamps` (saved to DB) + `captions` (in-memory list passed to video editor).

**Tunables:** `WHISPER_MODEL_SIZE` (tiny|base|small|medium|large), `WHISPER_DEVICE` (cpu|cuda).

## Stage 4 — Video Editing

**Module:** `src/video/video_editor.py`

This is the most complex stage. Walk through `VideoEditorService.render()`:

### 4a. Fetch stock clips
For each of the 6 `visual_keywords`:
- `PexelsFetcher.search(kw)` → up to 3 portrait HD clips
- If <3 results: `PixabayFetcher.search(kw)` fills the gap
- Each clip downloaded to `output/video/cache/<run_id>/`

Result: pool of ~12–18 unique clips.

### 4b. Build background
`_build_background(visual_keywords, total_duration)`:
1. Compute `MIN_SLOTS = ceil(total_duration / 2.2)` — enforce ≤2.2 sec per clip.
2. If pool < MIN_SLOTS: cycle the pool with shuffles, skipping back-to-back duplicates.
3. For each slot:
   - Subclip to `slot_duration`
   - Resize to 1080×1920
   - Apply Ken Burns (alternating zoom direction) + color grade (warm tone + film grain)
4. Concatenate → one continuous background track matching audio length.

### 4c. Hook card overlay
`_render_hook_card(text, duration=1.0)`:
- Renders the spoken hook as bold typography (1.0 sec) over a darkened background
- Adds a subtle 1.0× → 1.04× zoom-punch for energy
- Composited on top of the background for the first second
- Why: stock footage openings have the highest swipe-away rate; a typography card with motion holds the eye until the voice lands.

### 4d. Captions
`CaptionRenderer` (language-aware fonts):
- For each word: render a PIL frame with the active word highlighted (color shift + scale bump)
- Each word-clip duration = (next_word.start - this_word.start)
- Cross-fade-in for the first word of each caption group

### 4e. Compose + render
- `CompositeVideoClip([background, hook_card, *caption_clips])`
- Attach audio (`AudioFileClip`)
- Write MP4 with H.264 video + AAC audio @ 30fps

**Output:** `output/video/<run_id>_final.mp4` (~5–8 MB for 55 sec).

## Stage 4.25 — Thumbnail

**Module:** `src/video/thumbnail.py`

1. `extract_frame(video_path, timestamp=2.0)` pulls a frame ~2 sec in (past the hook card).
2. `generate_thumbnail(title, output_path, background_image=frame)`:
   - Blur + darken the frame
   - Render the title in bold with a random accent color stroke
   - Save 1280×720 JPEG (well under YouTube's 2MB limit)

**Output:** `output/video/thumbnails/<run_id>_thumb.jpg`.

## Stage 4.5 — Cleanup

`cleanup(audio_path, cache_dir)` removes:
- The voice MP3
- The entire clip cache directory

The final MP4 is kept until **after** upload.

## Stage 5 — YouTube Upload

**Module:** `src/uploader/youtube_uploader.py`

### 5a. OAuth
- If `token.json` exists with valid scopes → reuse
- If expired but refresh_token works → silent refresh
- Otherwise → `InstalledAppFlow` opens browser for consent (`youtube.force-ssl` scope)

### 5b. Upload
`upload(video_path, title, description, tags, privacy, publish_at)`:
- Builds metadata body (snippet + status)
- Auto-appends `#Shorts` to title if it fits
- Auto-appends `#Shorts #Short #viral` to description
- Deduplicates tags, caps at 500
- If `publish_at` set: forces `privacyStatus="private"` + `publishAt=<RFC3339>`
- Resumable upload in 1MB chunks (logs progress per chunk)
- Returns YouTube video ID

### 5c. Set thumbnail
`set_thumbnail(video_id, thumbnail_path)`:
- Soft-fails if the channel isn't verified for custom thumbnails (most new channels)
- Logs warning but doesn't kill the pipeline

### 5d. Auto-comment
`post_comment(video_id, comment_bait)`:
- Posts the bait question as a top-level comment from the channel
- Pinning is **not** supported by the YouTube API — log line includes the Studio URL so you can pin in 3 seconds manually

### 5e. Cleanup MP4
Final MP4 deleted from local disk (it's on YouTube now).

## Stage 6 — Done

- DB status → `COMPLETED`
- Result dict returned: `{doc_id, run_id, topic, video_path (deleted), youtube_id, youtube_url, comment_id, thumbnail_path, ...}`

## Failure paths

Any exception in any stage:
1. Logged with full traceback
2. DB status → `FAILED` with error message
3. Exception re-raised so the caller (CLI / Flask) knows

To retry: rerun with the same topic. Each run gets a new `doc_id` + `run_id` — old failed docs stay in DB for audit.
