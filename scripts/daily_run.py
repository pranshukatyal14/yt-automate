"""
Daily auto-upload runner.

Produces and publishes one niche-locked Short per day at the configured time.
Designed to be triggered by launchd (macOS) or cron (Linux).

Usage
-----
    python scripts/daily_run.py              # normal daily run
    python scripts/daily_run.py --force      # skip the already-posted-today guard
    python scripts/daily_run.py --dry-run    # render locally, no upload

Environment variables (set in .env)
------------------------------------
    POST_TIME   HH:MM in 24h format — publish time in the local timezone (default 20:00)
    POST_TZ     Hours offset from UTC, decimal OK e.g. +5.5 for IST (default 0)
    ALERT_WEBHOOK_URL  Slack/Discord-compatible webhook — POSTed on failure (optional)

Exit codes
----------
    0  video published (or already posted today)
    1  pipeline error  (alert fired if ALERT_WEBHOOK_URL is set)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── project root on sys.path ──────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

# ── Logging ───────────────────────────────────────────────────────────────────

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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _tz_offset() -> float:
    """Read POST_TZ from env; return float hours offset from UTC (default 0)."""
    raw = os.getenv("POST_TZ", "0").strip().lstrip("+")
    try:
        return float(raw)
    except ValueError:
        logger.warning("POST_TZ='%s' is not a valid number — defaulting to 0 (UTC)", raw)
        return 0.0


def _publish_at_rfc3339(tz_offset: float) -> str:
    """
    Build an RFC-3339 UTC publish timestamp for today's POST_TIME slot.
    If the slot has already passed today, schedules for tomorrow.
    Publish immediately (privacy=public, no publish_at) is recommended for v1
    since scheduled/private videos can't receive auto-comments — but this
    helper is kept for callers that want deferred publishing.
    """
    raw_time = os.getenv("POST_TIME", "20:00").strip()
    try:
        hh, mm = (int(x) for x in raw_time.split(":"))
    except (ValueError, TypeError):
        logger.warning("POST_TIME='%s' invalid — defaulting to 20:00", raw_time)
        hh, mm = 20, 0

    now_utc = datetime.now(timezone.utc)
    # Construct the desired local post time as UTC
    offset_td = timedelta(hours=tz_offset)
    local_now = now_utc + offset_td
    slot_local = local_now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    slot_utc   = slot_local - offset_td

    if slot_utc <= now_utc:
        slot_utc += timedelta(days=1)

    return slot_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _alert(message: str) -> None:
    """Fire a Slack/Discord-compatible webhook if ALERT_WEBHOOK_URL is set."""
    url = os.getenv("ALERT_WEBHOOK_URL", "").strip()
    if not url:
        return
    try:
        import urllib.request, json as _json
        payload = _json.dumps({"text": f":rotating_light: automate-yt daily run: {message}"}).encode()
        req = urllib.request.Request(url, data=payload,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
        logger.info("Alert sent to webhook.")
    except Exception as exc:
        logger.warning("Failed to send alert webhook: %s", exc)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Daily auto-upload runner")
    parser.add_argument("--force",   action="store_true",
                        help="Skip the already-posted-today guard")
    parser.add_argument("--dry-run", action="store_true",
                        help="Render locally without uploading to YouTube")
    args = parser.parse_args()

    tz_offset = _tz_offset()
    logger.info("=" * 60)
    logger.info("DAILY RUN START  UTC=%s  tz_offset=%+.1fh",
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), tz_offset)
    logger.info("=" * 60)

    # ── Idempotency guard ─────────────────────────────────────────────────────
    if not args.force and not args.dry_run:
        try:
            from src.db.models import VideoRepository
            repo = VideoRepository(
                mongo_uri=os.environ["MONGO_URI"],
                db_name=os.getenv("MONGO_DB_NAME", "automate_yt"),
            )
            already_done = repo.produced_today(tz_offset_hours=tz_offset)
            repo.close()
            if already_done:
                logger.info("Already produced a video today — skipping. (Use --force to override.)")
                return 0
        except Exception as exc:
            logger.warning("Idempotency check failed (%s) — proceeding anyway.", exc)

    # ── Run pipeline ──────────────────────────────────────────────────────────
    upload = not args.dry_run
    try:
        from src.orchestrator import run_pipeline

        result = run_pipeline(
            topic=None,     # auto-research trending niche topic
            upload=upload,
            # Publish immediately at run time — avoids the scheduled-private
            # video limitation that blocks auto-comments (see growth-playbook).
            publish_at=None,
        )

        youtube_url = result.get("youtube_url", "")
        if upload and youtube_url:
            logger.info("Daily video PUBLISHED: %s", youtube_url)
            logger.info(
                "ACTION REQUIRED: Reply to first 10 comments within 30 min — "
                "https://studio.youtube.com"
            )
            logger.info(
                "ACTION REQUIRED: Pin the bait comment — "
                "https://studio.youtube.com/video/%s/comments",
                result.get("youtube_id", ""),
            )
        elif args.dry_run:
            logger.info("Dry run complete. Video at: %s", result.get("video_path", ""))

        logger.info("=" * 60)
        logger.info("DAILY RUN COMPLETE")
        logger.info("=" * 60)
        return 0

    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        logger.exception("DAILY RUN FAILED: %s", msg)
        _alert(msg)
        return 1


if __name__ == "__main__":
    sys.exit(main())
