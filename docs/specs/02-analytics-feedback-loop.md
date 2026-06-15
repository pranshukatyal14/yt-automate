# Spec 02 — Close the Analytics → Scriptwriter Feedback Loop

**Priority:** P0 (highest-IQ lever — turns the pipeline into a learning system)
**Estimated effort:** 2–3 days
**Depends on:** Some uploaded videos with refreshed analytics (already supported)
**Owner:** _assign_

---

## Problem

The pipeline **measures but never learns.**

- [src/analytics/youtube_analytics.py](../../src/analytics/youtube_analytics.py) fetches `averageViewPercentage`, `ctr`, `views`, `subscribersGained`, etc.
- [src/db/models.py:163](../../src/db/models.py#L163) `set_analytics()` stores them on each video doc.
- **Nothing ever reads them back.** [src/script/scriptwriter.py](../../src/script/scriptwriter.py) generates every script blind to what actually performed on this channel.

Each video is a cold guess. We have all the data needed to make the scriptwriter learn which **hooks, topics, and styles** win on *this specific channel* — we just never feed it back.

## Goal

Before generating a new script, retrieve this channel's **top-performing past videos** (by retention + engagement) and inject their hooks/topics as few-shot "what works here" exemplars into the scriptwriter prompt. Also inject the **bottom performers** as an explicit "avoid these patterns" list.

This is the difference between a content farm and a channel that compounds.

## Acceptance criteria

- [ ] New `VideoRepository.top_performers(n, min_views)` returns the N best COMPLETED videos joined with their analytics, ranked by a composite score.
- [ ] New module/function `build_performance_context()` turns those into a compact prompt block: winning hooks, winning topics, losing patterns.
- [ ] `ScriptwriterService.write_script()` accepts an optional `performance_context` string and injects it into the system or user prompt.
- [ ] [orchestrator.py](../../src/orchestrator.py) builds the context (best-effort, never fatal) and passes it in.
- [ ] Cold-start safe: with <3 analyzed videos, the loop is a no-op and scripts generate exactly as today.
- [ ] A documented composite-score formula (below) with sensible defaults, env-tunable.
- [ ] Logged: which past winners informed each new script (for transparency/debugging).

## Composite score (default)

Rank past videos by a single number so "best" is unambiguous:

```
score = (averageViewPercentage / 100) * 0.5     # retention — the dominant Shorts signal
      + min(ctr / 10, 1.0)            * 0.2      # impressions → views (ctr is a %)
      + min(engagement_rate / 0.08, 1) * 0.2     # (likes+comments+shares)/views, capped at 8%
      + min(subs_per_1k_views / 5, 1) * 0.1      # subscriber pull
```

- All terms normalized to ~[0,1] so weights are meaningful.
- `engagement_rate = (likes + comments + shares) / max(views, 1)`.
- `subs_per_1k_views = subscribersGained / max(views/1000, 1)`.
- Require `views >= MIN_VIEWS_FOR_SIGNAL` (default 200) so noise from 5-view videos doesn't pollute the signal.
- Weights overridable via env (e.g. `SCORE_W_RETENTION`) but ship with the defaults above.

## Files to touch

| File | Change |
|---|---|
| [src/db/models.py](../../src/db/models.py) | Add `top_performers()` + `bottom_performers()` query helpers. |
| [src/analytics/youtube_analytics.py](../../src/analytics/youtube_analytics.py) | Add `score_video(doc)` + `build_performance_context(repo, n)`. |
| [src/script/scriptwriter.py](../../src/script/scriptwriter.py) | Add `performance_context` param; inject into prompt. |
| [src/orchestrator.py](../../src/orchestrator.py) | Build context before step 1, pass into `write_script`. |

## Approach

### 1. Repository query (`models.py`)
```python
def top_performers(self, n=5, min_views=200):
    """COMPLETED videos with analytics, joined script+metrics, newest-first fallback."""
    cur = self._col.find({
        "status": VideoStatus.COMPLETED.value,
        "analytics": {"$ne": None},
        "script_json": {"$ne": None},
    })
    return list(cur)   # scoring/ranking happens in the analytics layer
```
(Keep DB layer dumb; do scoring in the analytics layer so the formula lives in one place.)

### 2. Scoring + context builder (`youtube_analytics.py`)
- `score_video(doc) -> float | None` — pulls `doc["analytics"]`, applies the formula, returns `None` if `views < min_views`.
- `build_performance_context(repo, n=5) -> str`:
  - Fetch `repo.top_performers()`, score each, drop `None`, sort desc.
  - Take top N and bottom M (M=3).
  - Render a compact block:
    ```
    WHAT WORKS ON THIS CHANNEL (replicate the hook energy + topic angle):
      • [82% retention] hook: "Most men confuse silence for weakness. It's the opposite."  topic: handling disrespect
      • [78% retention] hook: "..."  topic: ...
    WHAT FAILED (do NOT repeat these patterns):
      • [19% retention] hook: "Today we're going to talk about discipline."  ← too slow, no pattern interrupt
    ```
  - Return `""` when fewer than 3 scored videos exist (cold-start no-op).

### 3. Scriptwriter injection (`scriptwriter.py`)
- `write_script(..., performance_context: str | None = None)`.
- When non-empty, prepend to the user prompt:
  ```
  CHANNEL MEMORY — these are real performance results from THIS channel.
  Treat the winners as your north star and avoid the losing patterns:
  {performance_context}
  ```
- Do **not** let the model copy a winning hook verbatim — instruct it to match the *energy and structure*, not the words (prevents repetitive uploads, which itself is a policy signal).

### 4. Orchestrator wiring
```python
performance_context = ""
try:
    from src.analytics.youtube_analytics import build_performance_context
    performance_context = build_performance_context(repo, n=5)
    if performance_context:
        logger.info("Feedback loop active — priming scriptwriter with channel winners")
except Exception as exc:
    logger.warning("Performance context unavailable (continuing cold): %s", exc)

script = scriptwriter.write_script(topic, style=style, lang=lang,
                                   performance_context=performance_context)
```
Wrap in try/except so analytics/DB hiccups never break a render.

## Test plan

1. **Cold start:** empty/2-video DB → `build_performance_context` returns `""`; scripts identical to today.
2. **Warm:** seed 5 docs with synthetic `analytics` (varied retention) → confirm ranking order and that the context block names the right winners/losers.
3. **End-to-end:** run `--no-upload` with warm DB; confirm the log line fires and the new hook echoes the winning *structure* (not verbatim copy).
4. **Resilience:** point `MONGO_URI` at a dead host → pipeline still renders (context just empty).

## Why this is the highest-IQ item

Niche-locking (Spec 01) gets you *consistency*. This gets you *direction*. After ~20 videos the scriptwriter stops guessing and starts amplifying your channel's actual winners — the mechanism that separates a 1k channel from a 100k one. It only works because Spec 01 keeps the niche stable enough for the signal to accumulate, so **ship 01 first, then 02.**

## Out of scope (follow-ups)

- Auto-tuning the score weights from outcomes (meta-learning).
- Per-format scoring once Spec 01 introduces multiple formats.
- Thumbnail/title A-B attribution (separate spec).
