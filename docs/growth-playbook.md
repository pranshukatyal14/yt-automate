# Growth Playbook

The code is only half the battle. These are the levers **no pipeline can pull for you**. Read this before you blame the algorithm.

## The honest baseline

YouTube Shorts is a discovery-by-retention machine. The algorithm ships your video to a small initial test audience and measures:

1. **Hook hold (sec 0–3)** — % of viewers still watching at 3 seconds
2. **Watch-through rate** — % of viewers who finish, or loop
3. **Engagement rate** — likes + comments + shares ÷ views
4. **Re-watch rate** — completions ÷ unique viewers (a number >1 means people rewatched)

A score above the niche median → wider distribution. Below → death. That's it. Every other "tip" is a derivative of moving one of those four numbers.

## Tier 1 — Things only you can do

### 1. Pick ONE tight sub-niche and commit for 60 days
**Why:** YouTube needs ~30 consecutive videos in one classifiable lane before it builds a "channel topic" signal. A wobbling channel never accumulates that signal — so it never gets pushed.

**How:** Set `CHANNEL_NICHE` in `.env`. Not "tech" → `iPhone hidden settings`. Not "psychology" → `dark psychology manipulation tactics`. Not "money" → `frugal living habits`. The narrower, the faster you compound.

**Test:** if a stranger watched 3 of your videos, could they describe your channel in 6 words? If not, you're too broad.

### 2. Reply to the first 10 real comments within 30 minutes
**Why:** YouTube uses early engagement velocity as a primary ranking signal. A reply triggers a notification, which brings the commenter back, which boosts session time, which the algorithm reads as "this video drives sessions" → it pushes harder.

**How:** Set a phone alarm 25 minutes after each publish. Reply to every real comment. Don't auto-generate replies — be human.

**Expected lift:** 2–3× initial reach when consistently applied.

### 3. Pin the auto-posted bait comment
The pipeline auto-posts your `comment_bait` as a top-level comment, but **the YouTube API doesn't expose pinning** (it never has). Pin manually:

1. Open YouTube Studio app on phone
2. Find the video → Comments
3. Long-press your auto-posted comment → Pin

Takes 3 seconds per video. Pinned comments get 5–10× more replies than unpinned ones — massive engagement multiplier.

### 4. Manually edit one line of every script before render
**Why:** YouTube's July 2025 "inauthentic / mass-produced content" policy targets channels that publish fully AI-generated content at scale. Detection signals include identical sentence cadence, predictable LLM phrasing, and uniform pacing across uploads.

**How:**
```bash
python -m src.orchestrator --topic "..." --no-upload
```
Open the rendered MP4. If anything sounds generic, regenerate (cheap — Gemini Flash is free-tier). The 30-second QA pass breaks the "AI farm" pattern.

### 5. Post 1 video/day at the same time
**Why:** YouTube's algorithm learns your slot. Consistent timing → predictable initial-audience pool → better data per test → faster promotion decisions.

**How:**
```bash
python -m src.orchestrator --schedule "20:00"
```
Best windows by niche (in your **audience's** timezone):
- Entertainment / lifestyle: 6–9 pm
- Educational / business: 7–9 am or 12–2 pm
- Late-night / dark content: 10 pm – 1 am

Pick one. Stick to it for 30 days. The data tells you which slot wins.

## Tier 2 — Things the pipeline already does for you

These are baked in — don't override them unless you know what you're doing:

| Lever | Where it lives |
|---|---|
| Hook in first 1 sec via bold typography card | `src/video/video_editor.py::_render_hook_card` |
| Karaoke per-word captions with active highlight | `src/video/video_editor.py::CaptionRenderer` |
| ≤2.2 sec per clip cadence | `src/video/video_editor.py::_build_background` |
| Loop architecture (CTA flows back into hook) | Enforced in scriptwriter prompt |
| `#Shorts` auto-appended to title + description | `src/uploader/youtube_uploader.py::upload` |
| Title keyword front-loaded in first 40 chars | Enforced in scriptwriter prompt |
| Spoken hook prepended to description | `src/orchestrator.py` |
| LUFS normalization to -14 (matches YouTube playback) | `src/audio/voice_service.py::_loudness_normalize` |
| AI-related words banned from metadata | Enforced in scriptwriter prompt |

## Tier 3 — Use the analytics feedback loop

Run weekly:
```bash
python -m src.analytics.youtube_analytics --refresh-all
```

Look for:

| Metric | Threshold | What to do |
|---|---|---|
| Avg view duration | > 70% of video length | Replicate this format 5×. Same hook structure, same pacing. |
| Avg view duration | < 30% | Watch your first 3 seconds in slow-mo. What's broken? Usually hook quality. |
| CTR (impressions → views) | < 3% | Title + thumbnail problem, not content. |
| CTR | > 8% | Thumbnail formula works. Use it again. |
| Re-watch rate | > 1.0× | You hit the loop architecture. Make more like this. |

**Rule:** if a format works (>70% retention), make 5 more in that exact format before trying something new. Most creators kill their own winners by chasing variety.

## Tier 4 — Realistic growth expectations

Honest math, no hype:

| Stage | Subscribers | Time (if disciplined) |
|---|---|---|
| Cold start | 0 → 1,000 | 30–90 days |
| First viral hit | 1k → 10k | 1 video, anywhere from day 5 to day 200 |
| Compounding | 10k → 100k | 6–12 months |
| Mid-channel | 100k → 1M | 12–24 months |
| Big channel | 1M → 10M | 2–4 years |
| Mega | 10M → 20M | 3–6 years |

The 20M target is a 3–6 year arc with high consistency, high content quality, and at least one breakthrough hit per quarter. The pipeline gets you to **operationally consistent** — the rest is taste, niche discipline, and luck.

## When to course-correct

If after **60 days** of daily uploads in one niche you're still under:
- 500 avg views per video, AND
- 30% avg view duration

The problem is **not** the pipeline. It's one of:
1. Niche is too broad or too saturated
2. Hook formula isn't pattern-interrupting (too generic)
3. Voice/pacing isn't matching the niche (e.g. high-energy voice for true crime is jarring)
4. Audience doesn't exist on Shorts for this topic

Switch the niche. Re-test for another 60 days. Don't add features — change content.

## Red flags to watch for

- **Subscriber growth without view growth** → you're attracting people who don't return. Niche mismatch.
- **High CTR + low retention** → clickbait. Thumbnail and content don't match. Algorithm will downrank.
- **Sudden drop to zero views** → either shadow-ban (rare, usually a policy strike) or you broke your niche signal. Check Studio for community guidelines warnings.
- **Comments calling out AI** → your content is too obviously generated. Apply Tier 1 step 4 harder.

## TL;DR

The pipeline gives you consistency. **Consistency × niche discipline × manual engagement = growth.** Skip any of the three and you're back to a hobby channel.
