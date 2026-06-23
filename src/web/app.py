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
logger = logging.getLogger(__name__)

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


# ── Slot schedule (IST = UTC+5.5) ─────────────────────────────────────────────
# GLOBAL-audience spread (geo data 2026-06-23: IN 30%, US 22% w/ best retention,
# GB 13% — ~41% US/UK). Slots span 17:30–23:30 IST to cover India evening AND
# stretch into UK evening prime (23:30 IST = 18:00 GMT) + US midday. Kept before
# midnight so scheduled-publish times never fall in the past on an afternoon run.
_SLOT_SCHEDULE_IST: dict[str, tuple[int, int]] = {
    "player_story": (17, 30),  # 17:30 IST — IN eve / GB 13:00 / US 08:00
    "debate":       (19, 30),  # 19:30 IST — IN eve / GB 15:00 / US 10:00
    "match_result": (21, 30),  # 21:30 IST — IN peak / GB 17:00 / US 12:00
    "fact":         (23, 30),  # 23:30 IST — IN late / GB 18:00 evening / US 13:00
}

_run_alls: dict[str, dict] = {}


def _calc_slot_publish_at(slot_type: str):
    """Return UTC datetime for slot's publish window, or None if already past."""
    from datetime import datetime, timedelta, timezone
    hh_mm = _SLOT_SCHEDULE_IST.get(slot_type)
    if not hh_mm:
        return None
    hh, mm = hh_mm
    tz_offset = float(os.getenv("POST_TZ", "5.5").lstrip("+"))
    offset_td = timedelta(hours=tz_offset)
    now_utc   = datetime.now(timezone.utc)
    local_now = now_utc + offset_td
    slot_local = local_now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    slot_utc   = slot_local - offset_td
    # If the slot window has already passed, schedule 15 min from now
    # so YouTube still receives it as a future-scheduled upload.
    if slot_utc <= now_utc:
        from datetime import timedelta as _td
        return now_utc + _td(minutes=15)
    return slot_utc


def _run_pipeline_bg(run_id: str, data: dict) -> None:
    thread_id = threading.current_thread().ident
    handler = _RunLogHandler(run_id, thread_id)
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    try:
        pub_utc_str = data.get("publish_at_utc")
        if pub_utc_str:
            from datetime import datetime, timezone as _tz
            publish_at = datetime.fromisoformat(pub_utc_str).replace(tzinfo=_tz.utc)
        else:
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


# ── Run-all (3 slots sequentially) ────────────────────────────────────────────

def _run_all_bg(run_all_id: str, slot_types: list[str]) -> None:
    for slot_type in slot_types:
        run_id    = uuid.uuid4().hex[:8]
        pub_at    = _calc_slot_publish_at(slot_type)
        pub_str   = pub_at.isoformat() if pub_at else None

        with _lock:
            _run_alls[run_all_id]["slots"][slot_type].update(
                {"run_id": run_id, "status": "running", "scheduled_for": pub_str}
            )
            _runs[run_id] = {
                "status":   "running",
                "stage":    0,
                "progress": {},
                "logs":     [],
                "result":   {},
                "error":    "",
                "_thread":  threading.current_thread(),
            }

        data = {
            "upload":         True,
            "style":          "factual",
            "lang":           "en",
            "video_type":     slot_type,
            "publish_at_utc": pub_str,
        }
        _run_pipeline_bg(run_id, data)   # blocks until this slot finishes

        with _lock:
            final = _runs[run_id]["status"]
            _run_alls[run_all_id]["slots"][slot_type]["status"] = final
            result = _runs[run_id].get("result", {})
            if result.get("youtube_url"):
                _run_alls[run_all_id]["slots"][slot_type]["youtube_url"] = result["youtube_url"]

    with _lock:
        all_ok = all(
            v["status"] == "complete"
            for v in _run_alls[run_all_id]["slots"].values()
        )
        _run_alls[run_all_id]["status"] = "complete" if all_ok else "partial_fail"

    # ── Analytics report for previous days' videos ─────────────────────────
    try:
        from src.analytics.daily_report import run_daily_report
        logger.info("Running daily analytics report…")
        run_daily_report(skip_analytics_fetch=False)
    except Exception as exc:
        logger.warning("Daily analytics report failed: %s", exc)


@app.route("/api/run-all-today", methods=["POST"])
def run_all_today():
    try:
        repo = VideoRepository(
            mongo_uri=os.environ["MONGO_URI"],
            db_name=os.getenv("MONGO_DB_NAME", "automate_yt"),
        )
        plan = repo.get_today_plan()
        repo.close()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    pending = [s["type"] for s in plan["slots"] if not s["done"]]
    if not pending:
        return jsonify({"done": True, "message": "All 4 slots already done today!"})

    # Backfill bait comments on previously-scheduled videos that are now public.
    try:
        from src.orchestrator import post_pending_comments
        threading.Thread(target=post_pending_comments, daemon=True).start()
    except Exception as exc:
        logger.warning("post_pending_comments failed to start: %s", exc)

    run_all_id = uuid.uuid4().hex[:8]
    slot_init  = {}
    for s in plan["slots"]:
        pub_at = _calc_slot_publish_at(s["type"])
        slot_init[s["type"]] = {
            "run_id":        None,
            "status":        "complete" if s["done"] else ("queued" if s["type"] in pending else "skipped"),
            "scheduled_for": pub_at.isoformat() if pub_at else None,
            "youtube_url":   f"https://youtu.be/{s['youtube_id']}" if s.get("youtube_id") else None,
        }

    with _lock:
        _run_alls[run_all_id] = {"status": "running", "slots": slot_init}

    t = threading.Thread(target=_run_all_bg, args=(run_all_id, pending), daemon=True)
    t.start()
    return jsonify({"run_all_id": run_all_id, "pending_slots": pending})


@app.route("/api/run-all-status/<run_all_id>")
def run_all_status(run_all_id: str):
    with _lock:
        state = _run_alls.get(run_all_id)
    if state is None:
        return jsonify({"error": "not found"}), 404

    result = {"status": state["status"], "slots": {}}
    for slot_type, slot_data in state["slots"].items():
        run_id   = slot_data.get("run_id")
        enriched = dict(slot_data)
        if run_id:
            with _lock:
                run = _runs.get(run_id, {})
            enriched["stage"]    = run.get("stage", 0)
            enriched["progress"] = run.get("progress", {})
        result["slots"][slot_type] = enriched
    return jsonify(result)


@app.route("/api/daily-report", methods=["POST"])
def trigger_daily_report():
    """Manually trigger analytics report. Pass ?test=1 to include today's videos."""
    test_mode = request.args.get("test", "0") == "1"
    def _bg():
        try:
            from src.analytics.daily_report import run_daily_report
            run_daily_report(skip_analytics_fetch=False, test_mode=test_mode)
        except Exception as exc:
            logger.warning("Manual daily report failed: %s", exc)
    threading.Thread(target=_bg, daemon=True).start()
    return jsonify({"status": "started", "test_mode": test_mode})


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
