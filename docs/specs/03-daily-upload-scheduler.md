# Spec 03 — Daily Auto-Upload Scheduler

**Priority:** P1 (consistency lever — automates the cadence the algorithm rewards)
**Estimated effort:** 1 day
**Depends on:** Spec 01 (niche lock) live, so daily auto-runs stay on-topic
**Owner:** _assign_

---

## Problem

Every video today requires a manual `python -m src.orchestrator` invocation. The growth playbook ([Tier 1 #5](../growth-playbook.md)) says posting **1 video/day at a fixed time** trains the algorithm's initial-audience pool and speeds up promotion decisions. We can't do that reliably by hand — and missed days reset the consistency signal.

There's a `--schedule` flag, but it only sets *one* video's YouTube publish time ([orchestrator.py `_parse_schedule`](../../src/orchestrator.py)). There is no recurring "produce + publish one fresh video every day" loop.

## Goal

A reliable daily runner that:
1. Generates ONE fresh niche-locked video per day (auto trend mode).
2. Publishes (or schedules) it at a consistent slot in the audience's timezone.
3. Survives failures (retries / alerts), never double-posts, and logs outcomes.

## Acceptance criteria

- [ ] A documented, OS-native scheduling path (macOS `launchd` plist or `cron`) that runs the pipeline once daily at a configurable time.
- [ ] A thin wrapper entrypoint (e.g. `scripts/daily_run.py`) that: loads env, runs `run_pipeline(topic=None)`, handles errors, writes a dated log, and exits non-zero on failure.
- [ ] Idempotency guard: if a video was already produced today, the wrapper skips (no accidental double-upload from overlapping triggers).
- [ ] Failure alert: on exception, log loudly + (optional) webhook/email hook so a silent failure doesn't break the streak unnoticed.
- [ ] `POST_TIME` and `POST_TZ` configurable via `.env`.
- [ ] Docs: a "Set up daily posting" section with copy-paste install commands.

## Files to touch / add

| File | Change |
|---|---|
| `scripts/daily_run.py` (new) | Wrapper entrypoint with idempotency + error handling. |
| `scripts/com.automateyt.daily.plist` (new, macOS) | `launchd` job template. |
| [.env.example](../../.env.example) | Add `POST_TIME`, `POST_TZ`, optional `ALERT_WEBHOOK_URL`. |
| [docs/getting-started.md](../../docs/getting-started.md) | "Daily posting" setup section. |
| [src/db/models.py](../../src/db/models.py) | Add `produced_today(tz)` helper for the idempotency check. |

## Approach

### 1. Idempotency (`models.py`)
```python
def produced_today(self, tz_offset_hours=0):
    """True if any non-FAILED doc was created in the current local day."""
    # compute local-day UTC bounds from tz_offset, count docs created in range
```
Wrapper calls this first and exits 0 (no-op) if a video already exists for today.

### 2. Wrapper (`scripts/daily_run.py`)
```python
def main():
    load_dotenv()
    repo = VideoRepository(...)
    if repo.produced_today(tz_offset):
        log("Already produced today — skipping."); return 0
    try:
        result = run_pipeline(topic=None, upload=True, publish_at=_todays_slot())
        log(f"Daily video published: {result.get('youtube_url')}")
        return 0
    except Exception as exc:
        alert(f"Daily run FAILED: {exc}")   # webhook if configured
        return 1
```
- `_todays_slot()` converts `POST_TIME`+`POST_TZ` to the RFC-3339 UTC string the uploader already accepts (reuse `_parse_schedule` logic, factored out of `orchestrator._cli`).
- Decide publish strategy: **publish immediately at run time** (run the cron AT the post slot) is simpler and avoids the "can't comment on scheduled/private video" branch in [orchestrator.py:331](../../src/orchestrator.py#L331). Recommended for v1.

### 3. Scheduling (macOS `launchd`)
Provide a plist template that runs `daily_run.py` at `POST_TIME` daily, with `StandardOutPath`/`StandardErrorPath` to `logs/daily.log`. Install command in docs:
```bash
cp scripts/com.automateyt.daily.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.automateyt.daily.plist
```
Also document the `cron` one-liner alternative for Linux/servers.

### 4. Alerts (optional, cheap)
If `ALERT_WEBHOOK_URL` is set, POST a one-line failure message (Slack/Discord-compatible). Otherwise rely on the dated log + non-zero exit.

## Operational guidance (put in docs)

- Run the job AT the chosen post slot, in the **audience's** timezone (playbook: educational/business 7–9am or 12–2pm; stoic/discipline content does well early morning + late evening).
- Pick ONE slot, hold it 30 days, then let Spec 02's analytics tell you the winning time.
- The machine must be awake at the slot (laptop sleep kills `launchd` timers) — note this; a cheap always-on box or cloud VM is the robust path once validated.

## Test plan

1. Run `daily_run.py` manually twice in a row → second run exits 0 with "already produced today."
2. Force an exception (bad API key) → exit 1, alert fires, log captured.
3. Install the plist, set `POST_TIME` 2 minutes out → confirm it fires and publishes.

## Out of scope

- Multi-video/day or batch backfill.
- Cloud deployment (separate infra spec) — but call out the laptop-sleep caveat so expectations are set.
