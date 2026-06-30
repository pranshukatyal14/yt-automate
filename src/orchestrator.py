"""
Orchestrator — ties the entire pipeline together.

Run
---
    python -m src.orchestrator --topic "5 Python tricks you didn't know"

Or from code:
    from src.orchestrator import run_pipeline
    run_pipeline("5 Python tricks you didn't know")

Pipeline steps
--------------
1. Insert PENDING document into MongoDB
2. SCRIPTING  — Gemini writes structured script
3. VOICING    — edge-tts converts script to MP3
4. TRANSCRIBING — faster-whisper extracts word timestamps
5. EDITING    — moviepy renders final MP4 with stock footage & captions
6. UPLOADING  — YouTube Data API v3 publishes the Short
7. COMPLETED  — MongoDB document updated with YouTube video ID
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

# Load .env before importing services (services read env vars at init time)
load_dotenv()

from src.audio.voice_service import VoiceService
from src.db.models import VideoRepository, VideoStatus
from src.notify import tg_stage, tg_success, tg_error
from src.script.scriptwriter import ScriptwriterService
from src.transcribe.transcription_service import TranscriptionService
from src.trend.trend_researcher import TrendResearcher
from src.uploader.youtube_uploader import YouTubeUploader
from src.video.thumbnail import extract_frame, generate_thumbnail
from src.video.video_editor import VideoEditorService

# ── Logging setup ──────────────────────────────────────────────────────────────

def _configure_logging() -> None:
    """Configure coloured, timestamped logging to both console and file."""
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    # Try coloured console output; fall back to plain if colorlog not installed
    try:
        import colorlog
        console_fmt = colorlog.ColoredFormatter(
            "%(log_color)s" + fmt,
            datefmt=datefmt,
            log_colors={
                "DEBUG":    "cyan",
                "INFO":     "green",
                "WARNING":  "yellow",
                "ERROR":    "red",
                "CRITICAL": "bold_red",
            },
        )
    except ImportError:
        console_fmt = logging.Formatter(fmt, datefmt=datefmt)

    file_fmt = logging.Formatter(fmt, datefmt=datefmt)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(console_fmt)

    file_handler = logging.FileHandler(log_dir / "pipeline.log", encoding="utf-8")
    file_handler.setFormatter(file_fmt)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(console_handler)
    root.addHandler(file_handler)


logger = logging.getLogger(__name__)


# ── Pipeline ───────────────────────────────────────────────────────────────────

def cleanup(
    audio_path: str | None = None,
    video_path: str | None = None,
    cache_dir: str | None = None,
) -> None:
    """
    Remove temporary/uploaded files after a pipeline run:
      - The run's voiceover MP3
      - The run's clip cache subfolder (cache_dir, or output/video/cache/ if not given)
      - The final rendered MP4 (only when video_path is provided, i.e. after upload)
    """
    if audio_path:
        p = Path(audio_path)
        if p.exists():
            p.unlink()
            logger.info("Deleted audio: %s", p)

    resolved_cache = (
        Path(cache_dir)
        if cache_dir
        else Path(os.getenv("OUTPUT_VIDEO_DIR", "output/video")) / "cache"
    )
    if resolved_cache.exists():
        shutil.rmtree(resolved_cache)
        logger.info("Deleted clip cache: %s", resolved_cache)

    if video_path:
        p = Path(video_path)
        if p.exists():
            p.unlink()
            logger.info("Deleted uploaded video: %s", p)


def _fetch_trending_hashtags(topic: str) -> str:
    """
    Use Gemini with Google Search grounding to fetch the top trending hashtags
    for the given topic right now. Always prepends #Shorts. Falls back to a
    safe static set if the API call fails.
    """
    try:
        from google import genai
        from google.genai import types as _types

        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        prompt = (
            f"What are the top 5 most viral and trending YouTube hashtags right now for a Short "
            f"about: '{topic}'? Include #Shorts as one of them. Return ONLY the hashtags on a "
            f"single line separated by spaces, e.g. #Shorts #FIFA #WorldCup2026 #Football #Messi. "
            f"No explanation, no extra text."
        )
        response = client.models.generate_content(
            model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            contents=prompt,
            config=_types.GenerateContentConfig(
                tools=[_types.Tool(google_search=_types.GoogleSearch())],
                temperature=0.0,
                thinking_config=_types.ThinkingConfig(thinking_budget=0),
            ),
        )
        parts = response.candidates[0].content.parts if response.candidates else []
        text_parts = [p.text for p in parts if hasattr(p, "text") and not getattr(p, "thought", False)]
        raw = ("".join(text_parts) if text_parts else response.text).strip()
        # Extract only tokens that look like hashtags
        hashtags = [w for w in raw.split() if w.startswith("#")]
        if "#Shorts" not in hashtags:
            hashtags.insert(0, "#Shorts")
        return " ".join(hashtags[:6])
    except Exception as exc:
        logger.warning("Trending hashtag fetch failed (%s) — using fallback", exc)
        return "#Shorts #Viral #YouTube"


def _parse_schedule(schedule_str: str) -> str:
    """Convert "HH:MM" or "YYYY-MM-DD HH:MM" to RFC 3339 UTC string for YouTube API."""
    now = datetime.now(timezone.utc)
    if len(schedule_str) <= 5:  # "HH:MM"
        t = datetime.strptime(schedule_str, "%H:%M").replace(
            year=now.year, month=now.month, day=now.day, tzinfo=timezone.utc
        )
        if t <= now:
            t += timedelta(days=1)
    else:  # "YYYY-MM-DD HH:MM"
        t = datetime.strptime(schedule_str, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def run_pipeline(
    topic: str | None = None,
    upload: bool = True,
    style: str = "factual",
    lang: str = "en",
    publish_at: str | None = None,
    video_type: str | None = None,
    avoid_topics: list[str] | None = None,
    use_ai_images: bool = False,
) -> dict:
    """
    Execute the full video generation pipeline.

    Parameters
    ----------
    topic      : The topic/title string. If None, auto-researches trending topics.
    upload     : If False, skip the YouTube upload step (useful during development).
    style      : "factual" or "story". Auto-detected from trend research when topic=None.
    lang       : ISO 639-1 code — "en", "hi", "es", "fr", "de", "pt", "ja", "ko", "zh", "ar".
    publish_at : RFC 3339 UTC string (e.g. "2026-04-24T20:00:00.000Z"). When set, YouTube
                 schedules the publish itself — video stays private until that time.

    Returns
    -------
    dict with doc_id, youtube_id (if uploaded), output paths, and trend info if auto-researched.
    """
    _configure_logging()

    # ── 0a. Auto trend research (when no topic provided) ──────────────────────
    trend_data: dict | None = None
    if topic is None:
        logger.info("=" * 60)
        logger.info("AUTO MODE — researching trending topics…")
        logger.info("=" * 60)
        researcher = TrendResearcher()
        trend_data = researcher.research(video_type=video_type, avoid_topics=avoid_topics)
        topic      = trend_data["winner_topic"]
        style      = trend_data["style"]   # override style from trend research
        logger.info("AUTO MODE picked → topic='%s'  style=%s", topic, style)
        logger.info("Rationale: %s", trend_data["rationale"])

    # ── 0b. Init services & DB ─────────────────────────────────────────────────
    repo = VideoRepository(
        mongo_uri=os.environ["MONGO_URI"],
        db_name=os.getenv("MONGO_DB_NAME", "automate_yt"),
    )
    doc_id = repo.insert(topic)
    run_id = str(uuid.uuid4())[:8]

    niche = (os.getenv("CHANNEL_NICHE") or "").strip()
    fmt   = (os.getenv("CHANNEL_FORMAT") or "").strip()
    if niche:
        repo.set_niche(doc_id, niche)
    if video_type:
        repo.set_video_type(doc_id, video_type)

    logger.info("=" * 60)
    logger.info("PIPELINE START  topic='%s'  doc_id=%s  run_id=%s", topic, doc_id, run_id)
    logger.info("=" * 60)

    result: dict = {"doc_id": doc_id, "run_id": run_id, "topic": topic}
    if trend_data:
        result["trend_top3"]  = trend_data.get("top3", [])
        result["trend_rationale"] = trend_data.get("rationale", "")

    try:
        # ── 0c. Analytics feedback context (best-effort, never fatal) ──────────
        performance_context = ""
        try:
            from src.analytics.youtube_analytics import build_performance_context
            performance_context = build_performance_context(repo)
        except Exception as _ctx_exc:
            logger.warning("Performance context unavailable (cold start): %s", _ctx_exc)

        # ── 1. Scriptwriting ───────────────────────────────────────────────────
        repo.set_status(doc_id, VideoStatus.SCRIPTING)
        logger.info("[1/6] Scripting…")
        logger.info("[PROGRESS:scripting:5]")

        scriptwriter = ScriptwriterService()
        logger.info("[PROGRESS:scripting:20]")
        script = scriptwriter.write_script(
            topic, style=style, lang=lang,
            niche=niche or None, fmt=fmt or None,
            performance_context=performance_context or None,
        )
        logger.info("[PROGRESS:scripting:85]")

        repo.set_script(doc_id, script)
        # Lead the description with the spoken hook — mobile users see the same
        # line they hear in the first second, which lifts the "tap more" rate.
        hook_line = (script.get("hook") or "").strip()
        body_desc = script["description"].strip()
        if hook_line and not body_desc.lower().startswith(hook_line.lower()[:30]):
            description = f"{hook_line}\n\n{body_desc}"
        else:
            description = body_desc

        # Fetch trending hashtags for this topic via Gemini Search grounding
        hashtag_line = _fetch_trending_hashtags(topic)
        description = f"{description}\n\n{hashtag_line}"

        repo.set_metadata(
            doc_id,
            title=script["title"],
            description=description,
            tags=script["tags"],
        )
        logger.info("[1/6] Script complete — keywords=%s", script["visual_keywords"])
        logger.info("[PROGRESS:scripting:100]")
        tg_stage("✍️ Scripting done", script["title"][:80], video_type)

        # ── 2. Voice synthesis ─────────────────────────────────────────────────
        logger.info("[2/6] Generating voiceover…")
        logger.info("[PROGRESS:voicing:5]")

        _VOICE_BY_TYPE = {
            "player_story": "en-US-BrianNeural",
            "match_result": "en-US-BrianNeural",
            "fact":         "en-US-AriaNeural",
        }
        selected_voice = _VOICE_BY_TYPE.get(video_type or "", None)
        voice_svc = VoiceService(lang=lang, voice=selected_voice)
        spoken_text = VoiceService.script_to_spoken_text(script)
        logger.info("[PROGRESS:voicing:20]")
        audio_path = voice_svc.synthesise(spoken_text, filename=f"{run_id}_voice")

        repo.set_audio(doc_id, str(audio_path))
        result["audio_path"] = str(audio_path)
        logger.info("[2/6] Audio saved: %s", audio_path)
        logger.info("[PROGRESS:voicing:100]")
        tg_stage("🎙️ Voiceover done", f"{audio_path.stem} — {spoken_text[:60]}…", video_type)

        # ── 3. Transcription ───────────────────────────────────────────────────
        logger.info("[3/6] Transcribing audio…")
        logger.info("[PROGRESS:transcribing:5]")

        transcriber = TranscriptionService()
        logger.info("[PROGRESS:transcribing:15]")
        word_timestamps = transcriber.transcribe(audio_path, language=lang)
        logger.info("[PROGRESS:transcribing:80]")
        captions = TranscriptionService.group_into_captions(word_timestamps, words_per_caption=4)

        repo.set_transcript(doc_id, word_timestamps)
        logger.info("[3/6] Transcription done — %d words, %d captions", len(word_timestamps), len(captions))
        logger.info("[PROGRESS:transcribing:100]")
        tg_stage("📝 Transcription done", f"{len(word_timestamps)} words — {len(captions)} captions", video_type)

        # ── 4. Video editing ───────────────────────────────────────────────────
        logger.info("[4/6] Rendering video…")
        logger.info("[PROGRESS:editing:2]")

        clip_cache = f"output/video/cache/{run_id}"
        editor = VideoEditorService(clip_cache_dir=clip_cache)
        video_path = editor.render(
            audio_path=audio_path,
            visual_keywords=script["visual_keywords"],
            captions=captions,
            output_filename=f"{run_id}_final",
            lang=lang,
            hook_text=script.get("hook"),
            use_ai_images=use_ai_images,
        )

        repo.set_video(doc_id, str(video_path))
        result["video_path"] = str(video_path)
        logger.info("[4/6] Video rendered: %s", video_path)
        logger.info("[PROGRESS:editing:100]")
        tg_stage("🎬 Rendering done", "Video ready — starting upload", video_type)

        # ── 4.25 Thumbnail generation ─────────────────────────────────────────
        try:
            thumb_dir = Path(os.getenv("OUTPUT_VIDEO_DIR", "output/video")) / "thumbnails"
            thumb_path = thumb_dir / f"{run_id}_thumb.jpg"
            # Pull a frame from ~25% in (past the hook card) for the blurred bg
            frame_path = thumb_dir / f"{run_id}_frame.jpg"
            extract_frame(video_path, timestamp=2.0, output_path=frame_path)
            generate_thumbnail(
                title=script["title"],
                output_path=thumb_path,
                background_image=frame_path if frame_path.exists() else None,
            )
            repo.set_thumbnail(doc_id, str(thumb_path))
            result["thumbnail_path"] = str(thumb_path)
            # Cleanup the intermediate frame
            if frame_path.exists():
                frame_path.unlink()
        except Exception as exc:
            logger.warning("Thumbnail generation skipped: %s", exc)
            thumb_path = None  # type: ignore[assignment]

        # ── 4.5 Cleanup temp files ─────────────────────────────────────────────
        logger.info("[4.5] Cleaning up audio and clip cache…")
        cleanup(audio_path=result.get("audio_path"), cache_dir=clip_cache)

        # ── 5. YouTube upload ──────────────────────────────────────────────────
        if upload:
            logger.info("[5/6] Uploading to YouTube…")
            logger.info("[PROGRESS:uploading:3]")
            doc = repo.get(doc_id)
            meta = doc["metadata"]

            uploader = YouTubeUploader()
            logger.info("[PROGRESS:uploading:8]")

            # Normalise publish_at: uploader needs RFC 3339 str; tg_success needs display str
            from datetime import datetime as _dt
            if isinstance(publish_at, _dt):
                publish_at_str = publish_at.strftime("%Y-%m-%dT%H:%M:%S.000Z")
                publish_at_display = publish_at.strftime("%Y-%m-%d %H:%M")
            elif isinstance(publish_at, str):
                publish_at_str = publish_at
                publish_at_display = publish_at
            else:
                publish_at_str = None
                publish_at_display = None

            youtube_id = uploader.upload(
                video_path=video_path,
                title=meta["title"],
                description=meta["description"],
                tags=meta["tags"],
                privacy="public",
                publish_at=publish_at_str,
            )
            repo.set_uploaded(doc_id, youtube_id)
            result["youtube_id"] = youtube_id
            result["youtube_url"] = f"https://youtu.be/{youtube_id}"
            if publish_at_str:
                logger.info("[5/6] Uploaded (scheduled for %s)! https://youtu.be/%s", publish_at_display, youtube_id)
            else:
                logger.info("[5/6] Uploaded! https://youtu.be/%s", youtube_id)
            logger.info("[PROGRESS:uploading:100]")
            tg_success(
                yt_url=f"https://youtu.be/{youtube_id}",
                topic=topic,
                scheduled_for=publish_at_display,
                video_type=video_type,
            )

            # ── 5b. Auto-comment from comment_bait ────────────────────────────
            # Skip when scheduled: video is private until publish_at and the
            # YouTube API rejects comments on private videos with 403.
            comment_bait = (script.get("comment_bait") or "").strip()
            if comment_bait and not publish_at:
                comment_id = uploader.post_comment(youtube_id, comment_bait)
                if comment_id:
                    repo.set_comment(doc_id, comment_id)
                    result["comment_id"] = comment_id
                    logger.info(
                        "[5b] Bait comment posted — pin manually in Studio: "
                        "https://studio.youtube.com/video/%s/comments",
                        youtube_id,
                    )
            elif comment_bait and publish_at:
                logger.info(
                    "[5b] Comment skipped (video is scheduled/private) — "
                    "post manually after publish: https://studio.youtube.com/video/%s/comments",
                    youtube_id,
                )

            # Delete the local MP4 now that it's safely on YouTube
            logger.info("[5/6] Deleting local video file after upload…")
            cleanup(video_path=result.get("video_path"))
        else:
            repo.set_status(doc_id, VideoStatus.COMPLETED)
            logger.info("[5/6] Upload skipped (upload=False)")
            logger.info("[PROGRESS:uploading:100]")

        # ── 6. Done ────────────────────────────────────────────────────────────
        logger.info("=" * 60)
        logger.info("PIPELINE COMPLETE  doc_id=%s", doc_id)
        logger.info("=" * 60)
        return result

    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
        logger.exception("PIPELINE FAILED at doc_id=%s — %s", doc_id, error_msg)
        repo.set_failed(doc_id, error_msg)
        tg_error(error_msg, video_type)
        raise

    finally:
        repo.close()


def post_pending_comments() -> dict:
    """
    Post bait comments on videos that were scheduled (private at upload time) and
    have since gone public. The upload-time auto-comment is skipped for scheduled
    videos because the API rejects comments on private videos — this backfills them.

    Finds COMPLETED videos that have a youtube_id + a comment_bait in their script
    but no comment_id yet, checks each is now public, and posts the bait comment.

    Returns a summary dict: {posted, skipped_private, skipped_no_bait, errors}.
    """
    from dotenv import load_dotenv
    load_dotenv()
    from src.db.models import VideoRepository
    from src.uploader.youtube_uploader import YouTubeUploader

    repo = VideoRepository(
        mongo_uri=os.environ["MONGO_URI"],
        db_name=os.getenv("MONGO_DB_NAME", "automate_yt"),
    )
    summary = {"posted": 0, "skipped_private": 0, "skipped_no_bait": 0, "errors": 0}
    try:
        # Only recent videos (last 3 days) — avoids a large comment burst that
        # could trip YouTube spam detection, and matches the real use case
        # (backfilling the previous run's now-public scheduled videos).
        from datetime import datetime as _dt2, timedelta as _td2, timezone as _tz2
        cutoff = _dt2.now(_tz2.utc) - _td2(days=3)
        candidates = list(repo._col.find({
            "metadata.youtube_id": {"$ne": None},
            "created_at": {"$gte": cutoff},
            "$or": [
                {"metadata.comment_id": None},
                {"metadata.comment_id": {"$exists": False}},
            ],
        }))
        if not candidates:
            return summary

        uploader = YouTubeUploader()
        # Batch-check public status
        ids = [c["metadata"]["youtube_id"] for c in candidates]
        public = set()
        for i in range(0, len(ids), 50):
            chunk = ids[i:i + 50]
            resp = uploader._service.videos().list(part="status", id=",".join(chunk)).execute()
            for it in resp.get("items", []):
                if it.get("status", {}).get("privacyStatus") == "public":
                    public.add(it["id"])

        for doc in candidates:
            yt_id = doc["metadata"]["youtube_id"]
            bait = ((doc.get("script_json") or {}).get("comment_bait") or "").strip()
            if not bait:
                summary["skipped_no_bait"] += 1
                continue
            if yt_id not in public:
                summary["skipped_private"] += 1
                continue
            try:
                comment_id = uploader.post_comment(yt_id, bait)
                if comment_id:
                    repo.set_comment(str(doc["_id"]), comment_id)
                    summary["posted"] += 1
                    logger.info("Backfilled bait comment on https://youtu.be/%s", yt_id)
                else:
                    summary["errors"] += 1
            except Exception as exc:
                summary["errors"] += 1
                logger.warning("Failed to post comment on %s: %s", yt_id, str(exc)[:120])

        if summary["posted"]:
            logger.warning("Pending comments posted: %d (skipped %d still-private)",
                           summary["posted"], summary["skipped_private"])
        return summary
    finally:
        repo.close()


# ── CLI entry point ────────────────────────────────────────────────────────────

def _cli() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Generate and upload a YouTube Short.\n\n"
            "AUTO MODE  (no --topic): Researches trending Google topics, picks the best\n"
            "           for a viral Short, and publishes automatically.\n\n"
            "MANUAL MODE (--topic): You control the topic.\n\n"
            "Examples:\n"
            "  python -m src.orchestrator                              # auto trending topic\n"
            "  python -m src.orchestrator --topic 'AI is changing everything'  # manual\n"
            "  python -m src.orchestrator --no-upload                  # auto, skip upload\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--topic",
        default=None,
        help=(
            "Topic / title for the Short. "
            "If omitted, the pipeline auto-researches the trendiest topic on Google right now."
        ),
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Render the video locally without uploading to YouTube",
    )
    parser.add_argument(
        "--style",
        choices=["factual", "story"],
        default="factual",
        help=(
            "Script style: 'factual' for tips/facts, 'story' for cinematic narrative. "
            "In auto mode this is overridden by Gemini's recommendation."
        ),
    )
    parser.add_argument(
        "--lang",
        default="en",
        metavar="LANG",
        help=(
            "Language code for script + voice. "
            "Options: en, hi, es, fr, de, pt, ja, ko, zh, ar  (default: en)"
        ),
    )
    parser.add_argument(
        "--schedule",
        default=None,
        metavar="TIME",
        help=(
            "Schedule the YouTube publish time. YouTube handles this natively — "
            "video is uploaded as private and auto-published at the given time. "
            "Formats: 'HH:MM' (today, UTC) or 'YYYY-MM-DD HH:MM' (UTC). "
            "Example: --schedule '20:00'  or  --schedule '2026-04-25 14:30'"
        ),
    )
    args = parser.parse_args()

    if args.topic is None:
        print("\n🔍 AUTO MODE — researching trending topics on Google…\n")
    else:
        print(f"\n📝 MANUAL MODE — topic: '{args.topic}'\n")

    publish_at = _parse_schedule(args.schedule) if args.schedule else None
    if publish_at:
        print(f"⏰ Scheduled publish: {publish_at} (YouTube will publish automatically)\n")

    result = run_pipeline(
        topic=args.topic,
        upload=not args.no_upload,
        style=args.style,
        lang=args.lang,
        publish_at=publish_at,
    )

    print("\n── Result ──────────────────────────────────────────")
    for k, v in result.items():
        if isinstance(v, list):
            print(f"  {k}:")
            for item in v:
                print(f"    • {item}")
        else:
            print(f"  {k}: {v}")
    print("────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    _cli()
