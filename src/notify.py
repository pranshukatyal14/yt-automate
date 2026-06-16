"""Telegram notification helpers — used by orchestrator and web app."""
from __future__ import annotations

import logging
import os
import urllib.request
import json

logger = logging.getLogger(__name__)

_SLOT_EMOJI = {"player_story": "⭐", "match_result": "⚽", "fact": "💡"}


def tg_send(text: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat  = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        return
    try:
        url     = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = json.dumps({"chat_id": chat, "text": text, "parse_mode": "HTML"}).encode()
        req     = urllib.request.Request(url, data=payload,
                                         headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as exc:
        logger.warning("Telegram notify failed: %s", exc)


def tg_stage(stage: str, detail: str = "", video_type: str | None = None) -> None:
    emoji = _SLOT_EMOJI.get(video_type or "", "🎬")
    tg_send(f"{emoji} <b>{stage}</b>{(' — ' + detail) if detail else ''}")


def tg_success(yt_url: str, topic: str, scheduled_for: str | None,
               video_type: str | None) -> None:
    emoji = _SLOT_EMOJI.get(video_type or "", "🎬")
    sched = f"\n⏰ <b>Goes live:</b> {scheduled_for} UTC" if scheduled_for else "\n▶️ <b>Published:</b> live now"
    tg_send(
        f"{emoji} <b>Upload complete!</b>\n"
        f"🔗 {yt_url}"
        f"{sched}\n"
        f"📝 {topic[:120]}{'…' if len(topic) > 120 else ''}"
    )


def tg_error(error: str, video_type: str | None = None) -> None:
    emoji = _SLOT_EMOJI.get(video_type or "", "🎬")
    tg_send(
        f"❌ <b>Pipeline failed</b> [{emoji} {video_type or 'unknown'}]\n"
        f"<code>{error[:400]}</code>"
    )
