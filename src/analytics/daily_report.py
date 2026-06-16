"""
Daily analytics report — runs automatically after each day's uploads complete.

Flow
----
1. Pull fresh metrics from YouTube Analytics API for all videos > 24h old
2. Store updated analytics in MongoDB
3. Send Gemini the full performance history → get structured improvements
4. Send report via Telegram
5. Return report dict for the UI

Note: YouTube Analytics data has a 24-48h delay, so metrics for videos
uploaded today will be empty — we only report on videos from yesterday+.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

_ANALYST_SYSTEM = """
You are a senior YouTube growth analyst with 15 years of experience growing
channels to millions of subscribers. You specialise in YouTube Shorts analytics
and know exactly what the algorithm rewards: retention, CTR, early swipe-away
rate, and comment velocity.

You speak in clear, direct, actionable bullets. No fluff, no "it depends."
Every suggestion must be specific and implementable tomorrow.
"""

_ANALYST_PROMPT = """
Here is the full performance history of a YouTube Shorts channel posting
FIFA World Cup 2026 content — 3 videos per day (player_story, match_result, fact).

VIDEOS (sorted by upload date, newest first):
{videos_block}

CHANNEL STATS:
- Total videos: {total_videos}
- Total views: {total_views}
- Average retention across all videos: {avg_retention:.1f}%
- Average CTR: {avg_ctr:.2f}%

Analyse this data and return a JSON object with these exact keys:

{{
  "summary": "2-3 sentence plain-English summary of what the data shows",
  "best_video_type": "player_story | match_result | fact — which type performs best on average",
  "worst_video_type": "which type consistently underperforms",
  "hook_patterns": ["pattern1 that works", "pattern2 that doesn't work"],
  "title_patterns": ["what title styles get high CTR"],
  "improvements": [
    "specific improvement 1 for tomorrow",
    "specific improvement 2",
    "specific improvement 3"
  ],
  "retention_diagnosis": "one sentence on why retention is high/low",
  "ctr_diagnosis": "one sentence on why CTR is high/low",
  "top_video_id": "youtube_id of the best-performing video",
  "bottom_video_id": "youtube_id of the worst-performing video"
}}

Be brutally honest. If something isn't working, say so directly.
Return ONLY the JSON object, no markdown, no commentary.
"""


def run_daily_report(skip_analytics_fetch: bool = False, test_mode: bool = False) -> dict[str, Any]:
    """
    Pull analytics, generate AI insights, send Telegram report.

    Parameters
    ----------
    skip_analytics_fetch : bool
        If True, skip the YouTube API call and use stored MongoDB data only.
        Useful when the token doesn't have analytics scopes yet.

    Returns
    -------
    dict with keys: summary, improvements, best_video_type, top_video, bottom_video, etc.
    """
    from dotenv import load_dotenv
    load_dotenv()

    from src.db.models import VideoRepository, VideoStatus
    from src.notify import tg_send

    repo = VideoRepository(
        mongo_uri=os.environ["MONGO_URI"],
        db_name=os.getenv("MONGO_DB_NAME", "automate_yt"),
    )

    try:
        # ── 1. Refresh analytics ───────────────────────────────────────────────
        if not skip_analytics_fetch:
            _refresh_analytics(repo, test_mode=test_mode)

        # ── 2. Load videos ─────────────────────────────────────────────────────
        query: dict = {
            "status": VideoStatus.COMPLETED.value,
            "metadata.youtube_id": {"$ne": None},
            "video_type": {"$in": ["player_story", "match_result", "fact"]},
        }
        if not test_mode:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
            query["created_at"] = {"$lt": cutoff}

        all_docs = list(repo._col.find(query, sort=[("created_at", -1)]))

        if not all_docs:
            logger.info("No videos older than 24h yet — skipping daily report")
            return {"status": "no_data", "message": "No videos older than 24h yet."}

        # ── 3. Build analysis block ────────────────────────────────────────────
        report = _generate_ai_report(all_docs)

        # ── 4. Send Telegram report ────────────────────────────────────────────
        _send_telegram_report(report, all_docs)

        return report

    finally:
        repo.close()


def _refresh_analytics(repo, test_mode: bool = False) -> None:
    """Pull fresh YouTube Analytics metrics for all eligible videos."""
    from src.analytics.youtube_analytics import YouTubeAnalytics

    query: dict = {
        "status": "COMPLETED",
        "metadata.youtube_id": {"$ne": None},
        "video_type": {"$in": ["player_story", "match_result", "fact"]},
    }
    if not test_mode:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        query["created_at"] = {"$lt": cutoff}

    # Only process the most recent 20 World Cup videos
    eligible = list(repo._col.find(query, sort=[("created_at", -1)], limit=20))

    if not eligible:
        return

    try:
        analytics = YouTubeAnalytics()
        for doc in eligible:
            vid = doc["metadata"]["youtube_id"]
            try:
                metrics = analytics.update_repo(repo, str(doc["_id"]), vid)
                logger.info("Analytics refreshed for %s — views=%s retention=%s%%",
                            vid,
                            metrics.get("lifetime_views", metrics.get("views", "?")),
                            metrics.get("averageViewPercentage", "?"))
            except Exception as exc:
                logger.warning("Could not refresh analytics for %s: %s", vid, exc)
    except Exception as exc:
        logger.warning("YouTubeAnalytics init failed (%s) — using cached data", exc)


def _call_ai(client, types, system: str, prompt: str) -> str:
    """Call Groq first (fast and free), fall back to Gemini."""
    import subprocess as _sp
    import json as _j
    import tempfile as _tf
    import os as _os

    # ── Groq via subprocess (avoids hanging HTTP library issues) ──────────────
    groq_key = _os.getenv("GROQ_API_KEY", "")
    if groq_key:
        try:
            payload = _j.dumps({
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": system + "\nReturn ONLY valid JSON, no markdown fences."},
                    {"role": "user",   "content": prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 1500,
            })
            result = _sp.run(
                ["curl", "-s", "--max-time", "30",
                 "-X", "POST", "https://api.groq.com/openai/v1/chat/completions",
                 "-H", "Content-Type: application/json",
                 "-H", f"Authorization: Bearer {groq_key}",
                 "-d", payload],
                capture_output=True, text=True, timeout=35,
            )
            if result.returncode == 0 and result.stdout:
                data = _j.loads(result.stdout)
                if "choices" in data:
                    logger.warning("Groq succeeded via curl ✓")
                    return data["choices"][0]["message"]["content"]
                logger.warning("Groq response had no choices: %s", result.stdout[:200])
            else:
                logger.warning("Groq curl failed rc=%d stderr=%s", result.returncode, result.stderr[:100])
        except Exception as exc_groq:
            logger.warning("Groq subprocess failed: %s", exc_groq)

    # ── Gemini fallback ───────────────────────────────────────────────────────
    for api_key in filter(None, [_os.getenv("GEMINI_API_KEY"), _os.getenv("GEMINI_API_KEY_ALT")]):
        for model in ["gemini-2.5-flash", "gemini-2.0-flash-lite"]:
            try:
                c = type(client)(api_key=api_key)
                resp = c.models.generate_content(
                    model=model, contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system, temperature=0.3,
                        max_output_tokens=1500, response_mime_type="application/json",
                        thinking_config=types.ThinkingConfig(thinking_budget=0),
                    ),
                )
                parts = resp.candidates[0].content.parts if resp.candidates else []
                text = ("".join(p.text for p in parts if hasattr(p, "text") and not getattr(p, "thought", False))
                        or resp.text).strip()
                logger.info("Gemini %s succeeded", model)
                return text
            except Exception as exc_g:
                logger.warning("Gemini %s failed: %s", model, str(exc_g)[:120])

    raise RuntimeError("All AI providers failed — Groq + all Gemini keys/models exhausted")


def _generate_ai_report(docs: list[dict]) -> dict[str, Any]:
    """Send video data to Gemini/Groq and get structured improvement suggestions."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    # Cap at 10 most recent to keep prompt manageable
    docs = docs[:10]

    lines = []
    total_views = 0
    retention_vals = []
    ctr_vals = []

    for doc in docs:
        a     = doc.get("analytics") or {}
        meta  = doc.get("metadata") or {}
        sc    = doc.get("script_json") or {}
        vtype = doc.get("video_type", "unknown")
        yt_id = meta.get("youtube_id", "")
        title = meta.get("title", doc.get("trend_topic", ""))[:80]
        hook  = sc.get("hook", "")[:60]

        views     = int(a.get("lifetime_views") or a.get("views") or 0)
        retention = float(a.get("averageViewPercentage") or 0)
        ctr       = float(a.get("impressionClickThroughRate") or 0)
        likes     = int(a.get("lifetime_likes") or a.get("likes") or 0)
        comments  = int(a.get("lifetime_comments") or a.get("comments") or 0)
        uploaded  = doc.get("created_at", "")
        if hasattr(uploaded, "strftime"):
            uploaded = uploaded.strftime("%Y-%m-%d")

        total_views += views
        if retention > 0:
            retention_vals.append(retention)
        if ctr > 0:
            ctr_vals.append(ctr)

        lines.append(
            f"- [{uploaded}] [{vtype}] yt:{yt_id}\n"
            f"  title: \"{title}\"\n"
            f"  hook: \"{hook}\"\n"
            f"  views:{views} retention:{retention:.0f}% ctr:{ctr:.2f}% "
            f"likes:{likes} comments:{comments}"
        )

    avg_retention = sum(retention_vals) / len(retention_vals) if retention_vals else 0
    avg_ctr       = sum(ctr_vals) / len(ctr_vals) if ctr_vals else 0

    prompt = _ANALYST_PROMPT.format(
        videos_block="\n".join(lines),
        total_videos=len(docs),
        total_views=total_views,
        avg_retention=avg_retention,
        avg_ctr=avg_ctr,
    )

    raw = _call_ai(client, types, _ANALYST_SYSTEM, prompt)
    clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.DOTALL).strip()
    report = json.loads(clean)

    report["total_views"]    = total_views
    report["total_videos"]   = len(docs)
    report["avg_retention"]  = round(avg_retention, 1)
    report["avg_ctr"]        = round(avg_ctr, 2)
    report["generated_at"]   = datetime.now(timezone.utc).isoformat()

    logger.warning("Daily report generated — best_type=%s improvements=%d",
                report.get("best_video_type"), len(report.get("improvements", [])))
    return report


def _send_telegram_report(report: dict, docs: list[dict]) -> None:
    from src.notify import tg_send

    top_id  = report.get("top_video_id", "")
    bot_id  = report.get("bottom_video_id", "")
    top_url = f"https://youtu.be/{top_id}" if top_id else "—"
    bot_url = f"https://youtu.be/{bot_id}" if bot_id else "—"

    improvements = "\n".join(
        f"  {i+1}. {imp}" for i, imp in enumerate(report.get("improvements", []))
    )
    hook_patterns = "\n".join(
        f"  • {p}" for p in report.get("hook_patterns", [])
    )

    msg = (
        f"📊 <b>Daily Analytics Report</b>\n"
        f"{'─'*30}\n"
        f"📹 Videos analysed: <b>{report.get('total_videos', 0)}</b>\n"
        f"👁️ Total views: <b>{report.get('total_views', 0):,}</b>\n"
        f"⏱️ Avg retention: <b>{report.get('avg_retention', 0)}%</b>\n"
        f"🖱️ Avg CTR: <b>{report.get('avg_ctr', 0)}%</b>\n\n"
        f"🏆 Best type: <b>{report.get('best_video_type', '—')}</b>\n"
        f"📉 Worst type: <b>{report.get('worst_video_type', '—')}</b>\n\n"
        f"🔍 <b>Summary</b>\n{report.get('summary', '')}\n\n"
        f"🎯 <b>Hook patterns</b>\n{hook_patterns}\n\n"
        f"✅ <b>Improvements for tomorrow</b>\n{improvements}\n\n"
        f"🥇 Best video: {top_url}\n"
        f"🥴 Worst video: {bot_url}"
    )

    tg_send(msg)
    logger.warning("Daily report sent via Telegram ✓")
