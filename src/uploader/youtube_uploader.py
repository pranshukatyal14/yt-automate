"""
YouTube Uploader — YouTube Data API v3.

Authentication uses OAuth 2.0 with a local client_secrets.json file.
The first run opens a browser for consent; subsequent runs use the saved
token file (token.json).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

logger = logging.getLogger(__name__)

# Full union of scopes shared with the analytics module. The uploader and
# YouTubeAnalytics share token.json — if the uploader requested only force-ssl
# it would strip the analytics scopes on every save, breaking the daily report.
# Requesting the union keeps a single token valid for upload + analytics.
SCOPES = [
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/youtube.readonly",
]
TOKEN_FILE      = "token.json"
API_SERVICE     = "youtube"
API_VERSION     = "v3"

CATEGORY_PEOPLE_BLOGS = "22"
CATEGORY_SCIENCE_TECH = "28"


class YouTubeUploader:
    """Handles OAuth and MP4 upload to YouTube."""

    def __init__(
        self,
        client_secrets_file: str | None = None,
        token_file: str = TOKEN_FILE,
    ) -> None:
        self._secrets_file = client_secrets_file or os.getenv(
            "YOUTUBE_CLIENT_SECRETS_FILE", "client_secrets.json"
        )
        self._token_file = token_file
        self._service = self._authenticate()

    # ── Authentication ─────────────────────────────────────────────────────────

    def _authenticate(self):
        creds: Credentials | None = None
        token_path = Path(self._token_file)

        if token_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(self._token_file, SCOPES)
            except ValueError:
                # Saved token's scopes don't match current SCOPES — drop and reconsent.
                logger.warning("Existing token has mismatched scopes — re-running OAuth")
                token_path.unlink(missing_ok=True)
                creds = None

        # Detect token whose scopes are a strict subset of what we now need,
        # or where scopes are None (can happen after refresh — treat as unverified).
        if creds:
            token_scopes = set(creds.scopes) if creds.scopes else set()
            missing = set(SCOPES) - token_scopes
            if missing or not token_scopes:
                logger.warning(
                    "Existing token missing or unverifiable scopes %s — re-running OAuth",
                    missing or "(scopes=None)",
                )
                token_path.unlink(missing_ok=True)
                creds = None

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                logger.info("Refreshing expired YouTube OAuth token…")
                creds.refresh(Request())
            else:
                logger.info("Starting OAuth flow — browser will open for consent…")
                flow = InstalledAppFlow.from_client_secrets_file(self._secrets_file, SCOPES)
                creds = flow.run_local_server(port=0)

            with open(self._token_file, "w") as f:
                f.write(creds.to_json())
            logger.info("OAuth token saved to %s", self._token_file)

        return build(API_SERVICE, API_VERSION, credentials=creds)

    # ── Upload ─────────────────────────────────────────────────────────────────

    def upload(
        self,
        video_path: str | Path,
        title: str,
        description: str,
        tags: list[str],
        category_id: str = CATEGORY_PEOPLE_BLOGS,
        privacy: str = "public",
        publish_at: str | None = None,
    ) -> str:
        """
        Upload *video_path* to YouTube with AI compliance metadata.

        Parameters
        ----------
        publish_at : RFC 3339 UTC string, e.g. "2026-04-24T20:00:00.000Z".
                     When set, the video is uploaded as private and YouTube
                     publishes it automatically at the scheduled time.

        Returns
        -------
        YouTube video ID (str), e.g. "dQw4w9WgXcQ"
        """
        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")

        # Description structure for Shorts:
        # - First 100 chars must hook the viewer (visible before "...more" on mobile)
        # - #Shorts hashtag required for YouTube to classify and surface in Shorts feed
        clean_desc = description.strip()
        full_description = (
            f"{clean_desc}\n\n"
            "#Shorts #Short #viral"
        )

        # Deduplicated tags, YouTube limit = 500
        all_tags = list(dict.fromkeys(["Shorts", "Short"] + tags))[:500]

        logger.info("Uploading '%s' to YouTube…", video_path.name)

        # Append #Shorts to title if it fits (YouTube uses this for Shorts feed classification)
        short_title = title.strip()
        if "#Shorts" not in short_title and len(short_title) + 8 <= 100:
            short_title = short_title + " #Shorts"

        status: dict = {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}
        if publish_at:
            status["privacyStatus"] = "private"
            status["publishAt"] = publish_at
            logger.info("Scheduled publish at %s (video will be private until then)", publish_at)

        body = {
            "snippet": {
                "title":        short_title[:100],
                "description":  full_description[:5000],
                "tags":         all_tags,
                "categoryId":   category_id,
                "defaultLanguage": "en",
            },
            "status": status,
        }

        media = MediaFileUpload(
            str(video_path),
            mimetype="video/mp4",
            resumable=True,
            chunksize=1 << 20,
        )

        try:
            request = self._service.videos().insert(
                part="snippet,status",
                body=body,
                media_body=media,
            )
            response = self._resumable_upload(request)
            video_id = response["id"]

            logger.info("Upload complete! https://youtu.be/%s", video_id)
            return video_id

        except HttpError as exc:
            logger.error("YouTube API error: %s", exc)
            raise

    # ── Thumbnail upload ───────────────────────────────────────────────────────

    def set_thumbnail(self, video_id: str, thumbnail_path: str | Path) -> bool:
        """
        Upload a custom thumbnail (≤2MB JPEG/PNG) for *video_id*.

        Requires the channel to be verified for custom thumbnails. Returns True
        on success, False if the API rejects it (e.g. unverified channel) so the
        pipeline doesn't fail the whole run on a thumbnail issue.
        """
        thumbnail_path = Path(thumbnail_path)
        if not thumbnail_path.exists():
            logger.warning("Thumbnail file missing: %s", thumbnail_path)
            return False

        try:
            media = MediaFileUpload(str(thumbnail_path), mimetype="image/jpeg")
            self._service.thumbnails().set(
                videoId=video_id, media_body=media,
            ).execute()
            logger.info("Thumbnail set for video %s", video_id)
            return True
        except HttpError as exc:
            logger.warning(
                "Thumbnail upload failed for %s — %s. "
                "Common cause: channel not verified for custom thumbnails.",
                video_id, exc,
            )
            return False

    # ── Comment posting ────────────────────────────────────────────────────────

    def post_comment(self, video_id: str, text: str) -> str | None:
        """
        Post a top-level comment on *video_id* as the authenticated channel.

        Note on pinning: the YouTube Data API does NOT expose comment pinning.
        The recommended workflow is to post the comment automatically (this
        method) and pin it manually from the YouTube Studio app — that takes
        ~3 seconds per video. We surface the comment_id in the return value
        so it can be linked from logs / a future browser-automation step.

        Returns the new comment_id on success, or None on failure.
        """
        if not text:
            return None
        try:
            resp = self._service.commentThreads().insert(
                part="snippet",
                body={
                    "snippet": {
                        "videoId": video_id,
                        "topLevelComment": {
                            "snippet": {"textOriginal": text}
                        },
                    }
                },
            ).execute()
            comment_id = resp.get("id")
            logger.info(
                "Posted comment on %s (id=%s) — pin manually in Studio for max boost",
                video_id, comment_id,
            )
            return comment_id
        except HttpError as exc:
            logger.warning("Comment post failed for %s: %s", video_id, exc)
            return None

    # ── Resumable upload helper ────────────────────────────────────────────────

    @staticmethod
    def _resumable_upload(request) -> dict:
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                raw_pct = int(status.progress() * 100)
                # Map raw 0-100 to 10-95 so the bar isn't at 100 before we've
                # done post-upload steps (thumbnail, comment).
                ui_pct = 10 + int(raw_pct * 0.85)
                logger.info("[PROGRESS:uploading:%d]", ui_pct)
                logger.info("Upload progress: %d%%", raw_pct)
        return response
