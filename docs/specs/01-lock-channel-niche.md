# Spec 01 — Lock the Channel Niche + Recurring Format

**Priority:** P0 (highest leverage, lowest effort)
**Estimated effort:** 0.5 day
**Depends on:** [Niche recommendation](00-niche-recommendation.md) sign-off
**Owner:** _assign_

---

## Problem

The channel ships with `CHANNEL_NICHE=` **empty** in [.env.example](../../.env.example). With no locked niche, the pipeline pulls a different trending topic every run, so YouTube never builds a "channel topic" classification signal — the prerequisite for algorithmic distribution. This is the single biggest reason the channel is stuck at 13 subs.

The growth playbook ([docs/growth-playbook.md](../growth-playbook.md), Tier 1 #1) already says this is the #1 lever. We're just not pulling it.

## Goal

1. Lock the channel to ONE tight sub-niche.
2. Go beyond a bare keyword — enforce a **recurring format** so videos are recognizably "a series," which is what drives binge + subscribe behavior and differentiates us from generic automated channels in the same lane.

## Acceptance criteria

- [ ] `CHANNEL_NICHE` is set (default in `.env.example` updated to the chosen niche, with a comment explaining the "tight format" principle).
- [ ] A new `CHANNEL_FORMAT` env var (optional) lets us pin a recurring episode template that the scriptwriter and trend researcher both respect.
- [ ] `TrendResearcher` produces topics that fit BOTH the niche AND the format (not just the niche).
- [ ] `ScriptwriterService` receives the niche + format context so titles/hooks are series-consistent.
- [ ] Running `python -m src.orchestrator --no-upload` 3× in a row produces 3 videos a stranger would recognize as the same series (the "6-word test").
- [ ] No regression: empty `CHANNEL_FORMAT` behaves exactly as today.

## Files to touch

| File | Change |
|---|---|
| [.env.example](../../.env.example) | Set `CHANNEL_NICHE` default; add `CHANNEL_FORMAT` with docs. |
| [src/trend/trend_researcher.py](../../src/trend/trend_researcher.py) | Read `CHANNEL_FORMAT`; inject into `_fetch_trends` + `_pick_best` prompts alongside the existing niche-lock block. |
| [src/script/scriptwriter.py](../../src/script/scriptwriter.py) | Accept `niche`/`format` params in `write_script`; inject a "series consistency" instruction into the user prompt. |
| [src/orchestrator.py](../../src/orchestrator.py) | Pass `CHANNEL_FORMAT` through to scriptwriter (niche is already read at line ~187). |

## Approach

### 1. `.env.example`
```bash
# ── Channel Strategy ──────────────────────────────────────────────────────────
# Lock the channel to ONE tight sub-niche so YouTube builds a strong topic signal.
# Narrower = faster compounding. Not "psychology" → "dark psychology tactics".
CHANNEL_NICHE=stoic philosophy and self-discipline for modern men

# Pin a RECURRING FORMAT so every video feels like the same series (drives binge +
# subscribe). Leave blank to let the model free-form within the niche.
# Example: "One stoic rule that fixes one specific modern problem (procrastination,
# being ignored, anxiety, disrespect). Always end by naming the next problem."
CHANNEL_FORMAT=One stoic rule that fixes one specific modern struggle
```

### 2. TrendResearcher
- In `__init__`, read `self._format = (os.getenv("CHANNEL_FORMAT") or "").strip()`.
- In `_fetch_trends`, when `_format` is set, append to the search prompt: ask for trending *struggles/problems* the format can attach to, not generic trends.
- In `_pick_best`, extend the existing `CHANNEL NICHE LOCK` block with a `FORMAT LOCK` instruction: the `winner_topic` must be phrased as an instance of the format.

### 3. ScriptwriterService
- Extend `write_script(self, topic, style="factual", lang="en", niche=None, fmt=None)`.
- When `fmt` is provided, append to the user prompt: a "SERIES CONSISTENCY" block instructing the title to follow the format's pattern and the hook to name the specific problem in the first 3 words.
- Keep all existing rules intact (loop architecture, AI-word ban, etc.).

### 4. Orchestrator
- It already reads `niche` at ~line 187. Add `fmt = (os.getenv("CHANNEL_FORMAT") or "").strip()` and pass `niche=niche, fmt=fmt` into `scriptwriter.write_script(...)`.

## Test plan

1. Set the env vars, run `--no-upload` 3×.
2. Confirm all 3 titles follow the format pattern and the niche.
3. Confirm empty `CHANNEL_FORMAT` reproduces current behavior (snapshot a script before/after).

## Out of scope

- Visual branding (intro sting, consistent color grade per series) — follow-up.
- Multi-format rotation — start with ONE format for 60 days per the playbook.
