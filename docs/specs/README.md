# Improvement Specs — Growth Plan (0 → Hero)

Strategic improvement package from the YouTube manager review (2026-06-01).
Hand each spec to a developer/agent as-is. **Ship in order — each unlocks the next.**

| # | Spec | Priority | Effort | Status |
|---|---|---|---|---|
| 00 | [Niche recommendation](00-niche-recommendation.md) | Decision | — | ⏳ Awaiting your sign-off |
| 01 | [Lock channel niche + recurring format](01-lock-channel-niche.md) | P0 | 0.5d | Ready (needs 00) |
| 02 | [Close analytics → scriptwriter feedback loop](02-analytics-feedback-loop.md) | P0 | 2–3d | Ready (ship after 01) |
| 03 | [Daily auto-upload scheduler](03-daily-upload-scheduler.md) | P1 | 1d | Ready (ship after 01) |

## The logic of the order

1. **00 → 01**: A locked, tight niche is the prerequisite for everything. Without it YouTube never classifies the channel and nothing else compounds.
2. **02**: Only works once the niche is stable enough for a performance signal to accumulate (~20 videos). It turns the pipeline from "guessing" into "amplifying our actual winners."
3. **03**: Automates the daily cadence so the channel-topic signal and the feedback loop both get fed consistently.

## Already done in this review

- ✅ **Background music removed** from the pipeline (per owner decision) — voiceover now ships clean. Touched `voice_service.py`, `setup_assets.py`, `generate_sfx.py`, and the architecture/pipeline/configuration docs.

## Deferred (Tier 2/3 — revisit after the above are live)

Series visual branding, hook A/B generation, semantic B-roll re-ranking, weekly analytics digest cron, multi-language fan-out of proven winners, thumbnail A/B. None of these matter until 01–03 are in place.

## Honest expectation-setting

These specs make the channel *operationally excellent and self-improving*. Reaching 1M on faceless automated Shorts still requires at least one genuine format breakthrough — which comes from niche taste + iterating on the data that Spec 02 surfaces. The system gets you to the start line in great shape; the data tells you where to sprint.
