"""
One-shot upload script for an already-rendered video.
Usage:
    .venv/bin/python _upload_existing.py <video_path> [doc_id]
"""
import os, sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from src.db.models import VideoRepository
from src.uploader.youtube_uploader import YouTubeUploader
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger(__name__)

def main():
    video_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    doc_id_arg = sys.argv[2] if len(sys.argv) > 2 else None

    if not video_path or not video_path.exists():
        print("Usage: .venv/bin/python _upload_existing.py <video_path> [doc_id]")
        print("\nAvailable videos:")
        for f in sorted(Path("output/video").glob("*.mp4")):
            print(f"  {f}")
        sys.exit(1)

    repo = VideoRepository(os.environ["MONGO_URI"], os.getenv("MONGO_DB_NAME", "automate_yt"))

    # Find the matching document
    run_id = video_path.stem.replace("_final", "")
    if doc_id_arg:
        from bson import ObjectId
        doc = repo.get(doc_id_arg)
    else:
        # Match by run_id embedded in filename (e.g. 519cba07_final.mp4 → run_id=519cba07)
        doc = repo._col.find_one(
            {"video_path": {"$regex": run_id}},
            sort=[("created_at", -1)],
        )

    if not doc:
        logger.error("No MongoDB document found for '%s'. Cannot fetch metadata.", video_path.name)
        sys.exit(1)

    meta = doc["metadata"]
    logger.info("Found doc: topic='%s'", doc["trend_topic"])
    logger.info("Title: %s", meta["title"])

    uploader = YouTubeUploader()
    youtube_id = uploader.upload(
        video_path=video_path,
        title=meta["title"],
        description=meta["description"],
        tags=meta["tags"],
        privacy="public",
    )

    repo.set_uploaded(str(doc["_id"]), youtube_id)
    repo.close()

    # Delete local video after successful upload
    video_path.unlink()
    logger.info("Local video deleted: %s", video_path)

    print(f"\n✅  Uploaded! https://youtu.be/{youtube_id}\n")

if __name__ == "__main__":
    main()
