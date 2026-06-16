"""
Daily auto-upload runner — slot-aware.

Runs one of the three daily slots (player_story / match_result / fact) and
uploads it to YouTube. Designed to be triggered by launchd (macOS) or cron.

Usage
-----
    python scripts/daily_run.py                          # auto-pick next pending slot
    python scripts/daily_run.py --slot match_result      # force a specific slot
    python scripts/daily_run.py --force                  # skip slot-done guard
    python scripts/daily_run.py --dry-run                # render locally, no upload

Schedule (IST — set in the three plist files)
---------------------------------------------
    08:00 IST  →  player_story  (Star Player Story)
    14:00 IST  →  match_result  (Match Result)
    20:00 IST  →  fact          (Shocking Fact)

Environment variables (set in .env)
------------------------------------
    POST_TZ            Hours offset from UTC, decimal OK e.g. +5.5 for IST
    ALERT_WEBHOOK_URL  Slack/Discord-compatible webhook — POSTed on failure

Exit codes
----------
    0  video published (or slot already done today)
    1  pipeline error  (alert fired if ALERT_WEBHOOK_URL is set)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

log_dir = ROOT / "logs"
log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_dir / "daily.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("daily_run")

SLOT_LABELS = {
    "player_story": "⭐ Star Player Story",
    "match_result": "⚽ Match Result",
    "fact":         "💡 Shocking Fact",
}


def _tz_offset() -> float:
    raw = os.getenv("POST_TZ", "0").strip().lstrip("+")
    try:
        return float(raw)
    except ValueError:
        logger.warning("POST_TZ='%s' is not a valid number — defaulting to 0 (UTC)", raw)
        return 0.0


def _alert(message: str) -> None:
    url = os.getenv("ALERT_WEBHOOK_URL", "").strip()
    if not url:
        return
    try:
        import json as _json
        import urllib.request
        payload = _json.dumps({"text": f":rotating_light: automate-yt: {message}"}).encode()
        req = urllib.request.Request(url, data=payload,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as exc:
        logger.warning("Failed to send alert webhook: %s", exc)


def _next_pending_slot(tz_offset: float) -> str | None:
    """Query MongoDB and return the next slot type that hasn't been done today."""
    from src.db.models import VideoRepository
    repo = VideoRepository(
        mongo_uri=os.environ["MONGO_URI"],
        db_name=os.getenv("MONGO_DB_NAME", "automate_yt"),
    )
    plan = repo.get_today_plan(tz_offset_hours=tz_offset)
    repo.close()
    return plan.get("next_type")


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily slot runner")
    parser.add_argument("--slot",    choices=["player_story", "match_result", "fact"],
                        help="Force a specific slot (default: auto-detect next pending)")
    parser.add_argument("--force",   action="store_true",
                        help="Skip the slot-already-done guard")
    parser.add_argument("--dry-run", action="store_true",
                        help="Render locally without uploading to YouTube")
    args = parser.parse_args()

    tz_offset = _tz_offset()
    logger.info("=" * 60)
    logger.info("DAILY RUN START  UTC=%s  tz_offset=%+.1fh",
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), tz_offset)
    logger.info("=" * 60)

    # ── Determine which slot to run ───────────────────────────────────────────
    if args.slot:
        video_type = args.slot
        logger.info("Slot forced via --slot: %s", SLOT_LABELS[video_type])
    else:
        try:
            video_type = _next_pending_slot(tz_offset)
        except Exception as exc:
            logger.warning("Could not check daily plan (%s) — defaulting to player_story", exc)
            video_type = "player_story"

        if video_type is None:
            logger.info("All 3 slots done for today. Nothing to do. (Use --force --slot X to override.)")
            return 0

        logger.info("Auto-detected next slot: %s", SLOT_LABELS.get(video_type, video_type))

    # ── Slot-done guard ───────────────────────────────────────────────────────
    if not args.force and not args.dry_run and not args.slot:
        # Already handled by _next_pending_slot returning None above
        pass

    # ── Run pipeline ──────────────────────────────────────────────────────────
    upload = not args.dry_run
    try:
        from src.orchestrator import run_pipeline

        result = run_pipeline(
            topic=None,
            upload=upload,
            publish_at=None,
            video_type=video_type,
        )

        youtube_url = result.get("youtube_url", "")
        yt_id       = result.get("youtube_id", "")

        if upload and youtube_url:
            logger.info("%s PUBLISHED: %s", SLOT_LABELS.get(video_type, video_type), youtube_url)
            logger.info("Pin the bait comment: https://studio.youtube.com/video/%s/comments", yt_id)
        elif args.dry_run:
            logger.info("Dry run complete. Video at: %s", result.get("video_path", ""))

        logger.info("=" * 60)
        logger.info("DAILY RUN COMPLETE — slot=%s", video_type)
        logger.info("=" * 60)
        return 0

    except Exception as exc:
        msg = f"[{video_type}] {type(exc).__name__}: {exc}"
        logger.exception("DAILY RUN FAILED: %s", msg)
        _alert(msg)
        return 1


if __name__ == "__main__":
    sys.exit(main())
