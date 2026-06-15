"""
Flask web UI for the automate-yt pipeline.
Start:  python run_ui.py
Open:   http://localhost:5000
"""
from __future__ import annotations

import logging
import os
import re
import threading
import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from flask import Flask, jsonify, render_template, request

from src.db.models import VideoRepository
from src.orchestrator import _parse_schedule, run_pipeline

app = Flask(__name__, template_folder="templates")

# run_id -> state dict
_runs: dict[str, dict] = {}
_lock = threading.Lock()

STAGE_KEYWORDS = {
    1: "[1/6]",
    2: "[2/6]",
    3: "[3/6]",
    4: "[4/6]",
    5: "[5/6]",
    6: "PIPELINE COMPLETE",
}

# Matches  [PROGRESS:scripting:42]  anywhere in a log line
_PROGRESS_RE = re.compile(r"\[PROGRESS:(\w+):(\d+)\]")


class _RunLogHandler(logging.Handler):
    def __init__(self, run_id: str, thread_id: int) -> None:
        super().__init__()
        self._run_id = run_id
        self._thread_id = thread_id  # only capture logs emitted by this run's thread
        self.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        # Ignore log records from other concurrent pipeline threads so that
        # "PIPELINE COMPLETE" (and stage markers) from Tab 1 never bleed into
        # Tab 2's or Tab 3's state.
        if record.thread != self._thread_id:
            return
        line = self.format(record)
        with _lock:
            run = _runs.get(self._run_id)
            if run is None:
                return
            run["logs"].append(line)
            msg = record.getMessage()
            for stage_num, marker in STAGE_KEYWORDS.items():
                if marker in msg:
                    run["stage"] = stage_num
                    break
            # Parse optional fine-grained progress:  [PROGRESS:editing:73]
            m = _PROGRESS_RE.search(msg)
            if m:
                run["progress"][m.group(1)] = int(m.group(2))


def _run_pipeline_bg(run_id: str, data: dict) -> None:
    thread_id = threading.current_thread().ident
    handler = _RunLogHandler(run_id, thread_id)
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    try:
        schedule_str = data.get("schedule", "").strip()
        publish_at = _parse_schedule(schedule_str) if schedule_str else None

        result = run_pipeline(
            topic=data.get("topic") or None,
            upload=data.get("upload", True),
            style=data.get("style", "factual"),
            lang=data.get("lang", "en"),
            publish_at=publish_at,
            video_type=data.get("video_type") or None,
        )

        # Keep only JSON-safe scalar fields
        safe_result = {
            k: v for k, v in result.items()
            if isinstance(v, (str, int, float, bool, type(None)))
        }
        if publish_at:
            safe_result["scheduled_for"] = publish_at

        with _lock:
            _runs[run_id]["status"] = "complete"
            _runs[run_id]["stage"] = 6
            _runs[run_id]["result"] = safe_result

    except BaseException as exc:
        with _lock:
            _runs[run_id]["status"] = "failed"
            _runs[run_id]["error"] = str(exc)
    finally:
        root_logger.removeHandler(handler)


@app.route("/api/daily-plan")
def daily_plan():
    try:
        repo = VideoRepository(
            mongo_uri=os.environ["MONGO_URI"],
            db_name=os.getenv("MONGO_DB_NAME", "automate_yt"),
        )
        plan = repo.get_today_plan()
        repo.close()
        return jsonify(plan)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/run", methods=["POST"])
def start_run():
    data = request.get_json(force=True)
    run_id = uuid.uuid4().hex[:8]

    t = threading.Thread(target=_run_pipeline_bg, args=(run_id, data), daemon=True)

    with _lock:
        _runs[run_id] = {
            "status": "running",
            "stage": 0,
            "progress": {},   # {stage_key: pct}  e.g. {"editing": 73}
            "logs": [],
            "result": {},
            "error": "",
            "_thread": t,
        }

    t.start()
    return jsonify({"run_id": run_id})


@app.route("/api/status/<run_id>")
def get_status(run_id: str):
    with _lock:
        run = _runs.get(run_id)
    if run is None:
        return jsonify({"error": "not found"}), 404

    # If thread has exited but status was never updated, the pipeline hung after
    # completing (e.g. repo.close() blocking). Treat as complete so the UI unsticks.
    thread = run.get("_thread")
    if run["status"] == "running" and thread is not None and not thread.is_alive():
        with _lock:
            if _runs[run_id]["status"] == "running":
                _runs[run_id]["status"] = "complete"
                _runs[run_id]["stage"] = 6
            # Re-read inside the lock so we always return the authoritative state.
            run = dict(_runs[run_id])

    # Strip internal fields before sending to client
    return jsonify({k: v for k, v in run.items() if not k.startswith("_")})
