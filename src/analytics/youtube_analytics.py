"""
YouTube Analytics — pulls per-video performance metrics for the feedback loop.

Why
---
The algorithm rewards retention (averageViewPercentage), early swipe-away
behaviour, and CTR. Without measuring these, the pipeline cannot learn which
hooks/topics actually work. This module fetches metrics for every uploaded
video so future scriptwriter runs can be primed with "what worked".

Usage
-----
    from src.analytics.youtube_analytics import YouTubeAnalytics
    analytics = YouTubeAnalytics()
    metrics = analytics.fetch(video_id="dQw4w9WgXcQ")

CLI
---
    python -m src.analytics.youtube_analytics --refresh-all
        # Refresh metrics for every COMPLETED video in MongoDB
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

# Read-only scopes — additive to the upload scope so token re-consent merges them.
ANALYTICS_SCOPES = [
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/youtube.readonly",
]

DEFAULT_TOKEN_FILE = "token.json"


class YouTubeAnalytics:
    """Wraps youtubeAnalytics v2 + youtube v3 stats endpoints."""

    def __init__(
        self,
        client_secrets_file: str | None = None,
        token_file: str = DEFAULT_TOKEN_FILE,
    ) -> None:
        self._secrets_file = client_secrets_file or os.getenv(
            "YOUTUBE_CLIENT_SECRETS_FILE", "client_secrets.json"
        )
        self._token_file = token_file
        creds = self._authenticate()
        self._yt = build("youtube", "v3", credentials=creds)
        self._yta = build("youtubeAnalytics", "v2", credentials=creds)

    # ── Auth ───────────────────────────────────────────────────────────────────

    def _authenticate(self) -> Credentials:
        """
        Re-uses the existing token.json if it already has the required analytics
        scopes; otherwise runs an additive consent flow that union's the scopes.
        """
        creds: Credentials | None = None
        token_path = Path(self._token_file)

        if token_path.exists():
            creds = Credentials.from_authorized_user_file(self._token_file)

        needs_reauth = (
            not creds
            or not creds.valid
            or not _scopes_satisfy(creds.scopes or [], ANALYTICS_SCOPES)
        )

        if needs_reauth:
            if creds and creds.expired and creds.refresh_token and _scopes_satisfy(
                creds.scopes or [], ANALYTICS_SCOPES
            ):
                creds.refresh(Request())
            else:
                logger.info(
                    "Analytics scopes not present — running OAuth consent "
                    "(union of upload + analytics scopes)…"
                )
                # Union with upload scope so the saved token works for both flows.
                full_scopes = list(
                    {*ANALYTICS_SCOPES, "https://www.googleapis.com/auth/youtube.upload",
                     "https://www.googleapis.com/auth/youtube.force-ssl"}
                )
                flow = InstalledAppFlow.from_client_secrets_file(
                    self._secrets_file, full_scopes
                )
                creds = flow.run_local_server(port=0)
                token_path.write_text(creds.to_json())
                logger.info("OAuth token updated with analytics scopes")

        return creds  # type: ignore[return-value]

    # ── Public API ─────────────────────────────────────────────────────────────

    def fetch(self, video_id: str, days: int = 28) -> dict[str, Any]:
        """
        Fetch retention + engagement metrics for a single video.

        Returns a flat dict with:
          views, likes, comments, shares, subscribersGained,
          averageViewDuration, averageViewPercentage, estimatedMinutesWatched,
          ctr (impressionClickThroughRate, %), impressions,
          fetched_at (ISO 8601 UTC).
        """
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=days)
        out: dict[str, Any] = {
            "video_id": video_id,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "window_days": days,
        }

        # ── youtubeAnalytics v2: retention + watch-time (core metrics) ─────────
        try:
            resp = self._yta.reports().query(
                ids="channel==MINE",
                startDate=start.isoformat(),
                endDate=end.isoformat(),
                metrics=(
                    "views,estimatedMinutesWatched,averageViewDuration,"
                    "averageViewPercentage,subscribersGained,likes,comments,shares"
                ),
                filters=f"video=={video_id}",
            ).execute()
            rows = resp.get("rows") or []
            headers = [h["name"] for h in resp.get("columnHeaders", [])]
            if rows:
                out.update(dict(zip(headers, rows[0])))
            else:
                logger.info("No analytics rows yet for %s (may be too new)", video_id)
        except HttpError as exc:
            logger.warning("Analytics query failed for %s: %s", video_id, exc)

        # ── Impressions + CTR (separate report — not always available) ─────────
        try:
            imp = self._yta.reports().query(
                ids="channel==MINE",
                startDate=start.isoformat(),
                endDate=end.isoformat(),
                metrics="impressions,impressionClickThroughRate",
                filters=f"video=={video_id}",
            ).execute()
            imp_rows = imp.get("rows") or []
            imp_headers = [h["name"] for h in imp.get("columnHeaders", [])]
            if imp_rows:
                out.update(dict(zip(imp_headers, imp_rows[0])))
        except HttpError as exc:
            logger.info("Impressions/CTR not available for %s: %s", video_id, str(exc)[:120])

        # ── youtube v3: lifetime aggregate counts (often more accurate near term) ─
        try:
            stats_resp = self._yt.videos().list(
                part="statistics,contentDetails", id=video_id
            ).execute()
            items = stats_resp.get("items", [])
            if items:
                stats = items[0].get("statistics", {})
                out["lifetime_views"] = int(stats.get("viewCount", 0))
                out["lifetime_likes"] = int(stats.get("likeCount", 0))
                out["lifetime_comments"] = int(stats.get("commentCount", 0))
        except HttpError as exc:
            logger.warning("videos.list failed for %s: %s", video_id, exc)

        return out

    def update_repo(self, repo, doc_id: str, video_id: str, days: int = 28) -> dict:
        """Convenience: fetch + persist via VideoRepository.set_analytics."""
        metrics = self.fetch(video_id=video_id, days=days)
        if hasattr(repo, "set_analytics"):
            repo.set_analytics(doc_id, metrics)
        else:
            logger.warning("Repo has no set_analytics — skipping persist")
        return metrics


def score_video(doc: dict, min_views: int = 200) -> float | None:
    """
    Composite performance score for a single video doc (0.0 – 1.0).

    Returns None when the doc lacks sufficient data (< min_views).

    Formula (weights sum to 1.0):
      retention  (averageViewPercentage / 100)     × 0.50
      ctr        (impressionClickThroughRate / 10)  × 0.20   [capped at 1]
      engagement ((likes+comments+shares) / views)  × 0.20   [capped at 8%]
      sub_pull   (subscribersGained / views×1000)   × 0.10   [capped at 5/1k]
    """
    a = doc.get("analytics") or {}
    views = int(a.get("views") or a.get("lifetime_views") or 0)
    if views < min_views:
        return None

    retention   = float(a.get("averageViewPercentage") or 0) / 100.0
    ctr_raw     = float(a.get("impressionClickThroughRate") or 0)
    likes       = int(a.get("likes") or a.get("lifetime_likes") or 0)
    comments    = int(a.get("comments") or a.get("lifetime_comments") or 0)
    shares      = int(a.get("shares") or 0)
    subs_gained = float(a.get("subscribersGained") or 0)

    ctr_score   = min(ctr_raw / 10.0, 1.0)
    eng_rate    = (likes + comments + shares) / max(views, 1)
    eng_score   = min(eng_rate / 0.08, 1.0)
    sub_score   = min(subs_gained / max(views / 1000.0, 0.001) / 5.0, 1.0)

    return (
        retention * 0.50
        + ctr_score * 0.20
        + eng_score * 0.20
        + sub_score * 0.10
    )


def build_performance_context(repo, n_winners: int = 5, n_losers: int = 3,
                               min_views: int = 200) -> str:
    """
    Query past videos, score them, and return a compact prompt block that
    the scriptwriter can use as few-shot channel memory.

    Returns an empty string when fewer than 3 scored videos exist (cold start).
    """
    try:
        docs = repo.videos_with_analytics(min_views=min_views)
    except Exception as exc:
        logger.warning("build_performance_context: DB query failed: %s", exc)
        return ""

    scored: list[tuple[float, dict]] = []
    for doc in docs:
        s = score_video(doc, min_views=min_views)
        if s is not None:
            scored.append((s, doc))

    if len(scored) < 3:
        logger.info(
            "Performance context: only %d scored videos (need ≥3) — cold start, skipping.",
            len(scored),
        )
        return ""

    scored.sort(key=lambda x: x[0], reverse=True)
    winners = scored[:n_winners]
    losers  = scored[-n_losers:]

    def _fmt_entry(score: float, doc: dict) -> str:
        a      = doc.get("analytics") or {}
        ret    = float(a.get("averageViewPercentage") or 0)
        hook   = (doc.get("script_json") or {}).get("hook", "").strip()
        topic  = (doc.get("trend_topic") or "").strip()
        title  = ((doc.get("metadata") or {}).get("title") or "").strip()
        views  = int(a.get("views") or a.get("lifetime_views") or 0)
        label  = title or topic
        return (
            f"  • [{ret:.0f}% retention | {views} views | score {score:.2f}] "
            f"topic: \"{label}\"  hook: \"{hook[:80]}\""
        )

    winner_lines = "\n".join(_fmt_entry(s, d) for s, d in winners)
    loser_lines  = "\n".join(_fmt_entry(s, d) for s, d in losers)

    context = (
        "CHANNEL MEMORY — real performance data from THIS channel.\n"
        "Winners (replicate the hook energy and topic angle — do NOT copy verbatim):\n"
        f"{winner_lines}\n"
        "Losers (avoid these hook patterns and topic framings):\n"
        f"{loser_lines}"
    )
    logger.info(
        "Performance context built — %d winners, %d losers from %d scored videos.",
        len(winners), len(losers), len(scored),
    )
    return context


def _scopes_satisfy(have: list[str], need: list[str]) -> bool:
    have_set = set(have)
    return all(s in have_set for s in need)


# ── CLI ────────────────────────────────────────────────────────────────────────

def _cli() -> None:
    from dotenv import load_dotenv
    load_dotenv()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )

    parser = argparse.ArgumentParser(description="Refresh YouTube analytics for uploaded videos")
    parser.add_argument("--video-id", help="Refresh metrics for one video ID")
    parser.add_argument("--refresh-all", action="store_true",
                        help="Refresh metrics for every COMPLETED video in MongoDB")
    parser.add_argument("--days", type=int, default=28, help="Lookback window in days")
    args = parser.parse_args()

    analytics = YouTubeAnalytics()

    if args.video_id and not args.refresh_all:
        metrics = analytics.fetch(video_id=args.video_id, days=args.days)
        print(metrics)
        return

    if args.refresh_all:
        from src.db.models import VideoRepository, VideoStatus
        repo = VideoRepository(
            mongo_uri=os.environ["MONGO_URI"],
            db_name=os.getenv("MONGO_DB_NAME", "automate_yt"),
        )
        try:
            cursor = repo._col.find({"status": VideoStatus.COMPLETED.value})
            count = 0
            for doc in cursor:
                vid = (doc.get("metadata") or {}).get("youtube_id")
                if not vid:
                    continue
                metrics = analytics.update_repo(repo, str(doc["_id"]), vid, days=args.days)
                count += 1
                logger.info(
                    "doc=%s vid=%s views=%s avgPct=%s",
                    doc["_id"], vid,
                    metrics.get("views"), metrics.get("averageViewPercentage"),
                )
            logger.info("Refreshed analytics for %d videos", count)
        finally:
            repo.close()


if __name__ == "__main__":
    _cli()
