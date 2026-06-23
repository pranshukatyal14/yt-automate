"""
MongoDB document schema and repository for the pipeline.

Collections
-----------
videos : one document per Shorts video in the pipeline.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from bson import ObjectId
from pymongo import MongoClient, ReturnDocument
from pymongo.collection import Collection

logger = logging.getLogger(__name__)


# ── Status enum ────────────────────────────────────────────────────────────────

class VideoStatus(str, Enum):
    PENDING       = "PENDING"
    SCRIPTING     = "SCRIPTING"
    VOICING       = "VOICING"
    TRANSCRIBING  = "TRANSCRIBING"
    EDITING       = "EDITING"
    UPLOADING     = "UPLOADING"
    COMPLETED     = "COMPLETED"
    FAILED        = "FAILED"


# ── Document helpers ───────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


DAILY_SLOTS = [
    {"type": "player_story", "label": "Star Player Story",  "emoji": "⭐"},
    {"type": "debate",       "label": "Rivalry Debate",     "emoji": "🔥"},
    {"type": "match_result", "label": "Match Result",        "emoji": "⚽"},
    {"type": "fact",         "label": "Shocking Fact",       "emoji": "💡"},
]


def new_video_document(trend_topic: str) -> dict[str, Any]:
    """
    Factory — returns a fresh VideoDocument dict ready for insertion.

    Schema
    ------
    trend_topic     : str            — the input topic/trend title
    script_json     : dict | None    — {hook, body, call_to_action, visual_keywords}
    audio_path      : str | None     — local path to generated MP3
    video_path      : str | None     — local path to rendered MP4
    transcript      : list[dict]     — [{word, start, end}, ...]
    status          : VideoStatus
    error_message   : str | None
    metadata        : dict           — {title, description, tags, youtube_id}
    created_at      : datetime (UTC)
    updated_at      : datetime (UTC)
    """
    return {
        "trend_topic":   trend_topic,
        "video_type":    None,
        "niche":         None,
        "script_json":   None,
        "audio_path":    None,
        "video_path":    None,
        "thumbnail_path": None,
        "transcript":    [],
        "status":        VideoStatus.PENDING,
        "error_message": None,
        "metadata": {
            "title":       None,
            "description": None,
            "tags":        [],
            "youtube_id":  None,
            "youtube_url": None,
            "comment_id":  None,
        },
        "analytics":     None,
        "created_at": _now(),
        "updated_at": _now(),
    }


# ── Repository ─────────────────────────────────────────────────────────────────

class VideoRepository:
    """Thin wrapper around the *videos* collection."""

    def __init__(self, mongo_uri: str, db_name: str) -> None:
        self._client: MongoClient = MongoClient(mongo_uri)
        self._col: Collection = self._client[db_name]["videos"]
        self._ensure_indexes()
        logger.info("VideoRepository connected to MongoDB database '%s'", db_name)

    # ── Index setup ────────────────────────────────────────────────────────────

    def _ensure_indexes(self) -> None:
        self._col.create_index("status")
        self._col.create_index("created_at")
        self._col.create_index("trend_topic")

    # ── CRUD ───────────────────────────────────────────────────────────────────

    def insert(self, topic: str) -> str:
        """Insert a new PENDING video document; return its string id."""
        doc = new_video_document(topic)
        result = self._col.insert_one(doc)
        _id = str(result.inserted_id)
        logger.info("Inserted video document id=%s topic='%s'", _id, topic)
        return _id

    def get(self, doc_id: str) -> dict[str, Any] | None:
        return self._col.find_one({"_id": ObjectId(doc_id)})

    def set_status(self, doc_id: str, status: VideoStatus) -> None:
        self._update(doc_id, {"status": status})
        logger.info("doc_id=%s → status=%s", doc_id, status)

    def set_failed(self, doc_id: str, error: str) -> None:
        self._update(doc_id, {
            "status":        VideoStatus.FAILED,
            "error_message": error,
        })
        logger.error("doc_id=%s FAILED: %s", doc_id, error)

    def set_script(self, doc_id: str, script_json: dict) -> None:
        self._update(doc_id, {
            "script_json": script_json,
            "status":      VideoStatus.VOICING,
        })

    def set_audio(self, doc_id: str, audio_path: str) -> None:
        self._update(doc_id, {
            "audio_path": audio_path,
            "status":     VideoStatus.TRANSCRIBING,
        })

    def set_transcript(self, doc_id: str, transcript: list[dict]) -> None:
        self._update(doc_id, {
            "transcript": transcript,
            "status":     VideoStatus.EDITING,
        })

    def set_video(self, doc_id: str, video_path: str) -> None:
        self._update(doc_id, {
            "video_path": video_path,
            "status":     VideoStatus.UPLOADING,
        })

    def set_uploaded(self, doc_id: str, youtube_id: str) -> None:
        self._update(doc_id, {
            "metadata.youtube_id":  youtube_id,
            "metadata.youtube_url": f"https://youtu.be/{youtube_id}",
            "status":               VideoStatus.COMPLETED,
        })

    def set_thumbnail(self, doc_id: str, thumbnail_path: str) -> None:
        self._update(doc_id, {"thumbnail_path": thumbnail_path})

    def set_niche(self, doc_id: str, niche: str) -> None:
        self._update(doc_id, {"niche": niche})

    def set_video_type(self, doc_id: str, video_type: str) -> None:
        self._update(doc_id, {"video_type": video_type})

    def get_today_plan(self, tz_offset_hours: float = 5.5) -> dict:
        """
        Return today's content plan: which slots are done and what's next.
        tz_offset_hours defaults to IST (+5.5).
        """
        from datetime import timedelta
        now_utc = _now()
        local_now = now_utc + timedelta(hours=tz_offset_hours)
        day_start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end_local = day_start_local + timedelta(days=1)
        day_start_utc = day_start_local - timedelta(hours=tz_offset_hours)
        day_end_utc = day_end_local - timedelta(hours=tz_offset_hours)

        docs = list(self._col.find({
            "status": {"$ne": VideoStatus.FAILED.value},
            "created_at": {"$gte": day_start_utc, "$lt": day_end_utc},
        }, sort=[("created_at", 1)]))

        done_types = [d.get("video_type") for d in docs if d.get("video_type")]

        slots = []
        next_type = None
        for slot in DAILY_SLOTS:
            done = slot["type"] in done_types
            if done:
                doc = next((d for d in docs if d.get("video_type") == slot["type"]), None)
                yt_id = doc.get("metadata", {}).get("youtube_id") if doc else None
                slots.append({**slot, "done": True, "youtube_id": yt_id})
            else:
                if next_type is None:
                    next_type = slot["type"]
                slots.append({**slot, "done": False})

        return {"slots": slots, "next_type": next_type, "total_done": len(done_types)}

    def set_comment(self, doc_id: str, comment_id: str) -> None:
        self._update(doc_id, {"metadata.comment_id": comment_id})

    def set_analytics(self, doc_id: str, analytics: dict) -> None:
        self._update(doc_id, {"analytics": analytics})

    def videos_with_analytics(self, min_views: int = 0) -> list[dict[str, Any]]:
        """Return all COMPLETED docs that have analytics data and a script."""
        query: dict[str, Any] = {
            "status":      VideoStatus.COMPLETED.value,
            "analytics":   {"$ne": None},
            "script_json": {"$ne": None},
        }
        if min_views > 0:
            query["analytics.views"] = {"$gte": min_views}
        return list(self._col.find(query, sort=[("created_at", -1)]))

    def produced_today(self, tz_offset_hours: float = 0) -> bool:
        """
        Return True if any non-FAILED video doc was created during the current
        local calendar day (derived from tz_offset_hours relative to UTC).
        Used by the daily runner as an idempotency guard.
        """
        from datetime import timedelta
        now_utc   = _now()
        local_now = now_utc + timedelta(hours=tz_offset_hours)
        # Start/end of the current local day expressed in UTC
        day_start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end_local   = day_start_local + timedelta(days=1)
        day_start_utc   = day_start_local - timedelta(hours=tz_offset_hours)
        day_end_utc     = day_end_local   - timedelta(hours=tz_offset_hours)
        count = self._col.count_documents({
            "status":     {"$ne": VideoStatus.FAILED.value},
            "created_at": {"$gte": day_start_utc, "$lt": day_end_utc},
        })
        return count > 0

    def set_metadata(self, doc_id: str, title: str, description: str, tags: list[str]) -> None:
        self._update(doc_id, {
            "metadata.title":       title,
            "metadata.description": description,
            "metadata.tags":        tags,
        })

    # ── Internal ───────────────────────────────────────────────────────────────

    def _update(self, doc_id: str, fields: dict) -> None:
        fields["updated_at"] = _now()
        self._col.find_one_and_update(
            {"_id": ObjectId(doc_id)},
            {"$set": fields},
            return_document=ReturnDocument.AFTER,
        )

    def close(self) -> None:
        self._client.close()
