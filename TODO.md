# LogiSnap — Action Items & Backlog

> Living list of what's shipped, what's queued, and why. Update as things move.
> Last updated: 2026-06-26

---

## 📅 SCHEDULED — Monday 2026-06-29: Real-data integration (HIGH priority)

**Build:** plug a real football API into the trend researcher so topics come from
ACTUAL World Cup 2026 results, not AI-fabricated events. Fixes the credibility ceiling +
lets us ride real viral moments. Time-sensitive: WC ends ~July 19, so payback window is short.

**Key context (researched 2026-06-26):**
- The pipeline ALREADY has Gemini Google-Search grounding, but keeps hitting Gemini's 429
  rate limit and falling back to Groq, which fabricates from training data. So we default to
  fabrication when quota runs out. A football API makes real-data sourcing RELIABLE.
- Free APIs: football-data.org (free forever, top comps), API-Football (~100 req/day free),
  TheStatsAPI (all 104 WC2026 fixtures, no signup). Start with football-data.org.
- Build plan: fetcher module → feed real fixtures/results/scorers as topic candidates into
  trend researcher → handle "no match today" gracefully → test → deploy.
- Deploy AFTER current package (Jun24-26 changes) results land so we don't muddy measurement.

**DECIDED — real match CLIPS = hard NO** (do NOT switch from stock/AI footage): FIFA Content ID
auto-claims all WC broadcast footage → strikes + the "reused content" policy PERMANENTLY blocks
YPP monetization (our whole goal). Engagement upgrade path instead = AI-generated football
visuals (copyright-free), NOT real clips. Researched/decided 2026-06-26.

---

## ✅ SHIPPED (live in pipeline)

- **Retention rewrite** — open-loop hook + mid-video re-hook + tighter scripts (90-120 words). Hook card 0.8s→0.5s. → retention 25%→52%.
- **Topic bias → controversy/drama** about superstars (negative/failure angles win). Both Gemini + Groq paths.
- **Comments fixed** — `post_pending_comments()` backfills bait on now-public videos (was always skipped on scheduled videos). Debate-starter bait copy.
- **SUBSCRIBE badge** — red pill overlay last 3s of every video + FOMO subscribe CTA.
- **Evening + global time slots** — 17:30–23:30 IST (covers India evening + UK prime + US midday). 4 slots incl. new **debate** slot.
- **Hot-take fact slot** — replaced dead "fact" slot with divisive debate topics. VALIDATED (Jun22 hot-take 813 views vs old 78-489).
- **Channel-page optimization** — description, keywords, unsubscribed trailer (Ronaldo BLANKED), "🔥 World Cup 2026 Drama" playlist. All API-set, reversible.
- **Audience re-tune → men 25-50** (2026-06-24) — `.env` niche, trend researcher + scriptwriter now target adult male fans who grew up with these stars (was wrongly "16-35 teens"). Added legacy/nostalgia + US/Western angles.
- **NO POST-HOOK DIP rule** (2026-06-24) — retention curves proved weak videos lose ~70% of viewers in the 2s right after the hook (hook→body transition). Scriptwriter now requires the first body line to ESCALATE, never explain/set-up. Targets the single biggest drop point.
- **Elimination/stakes bias** (2026-06-24) — knockouts/shock-exits drive SUBS best ("Turkiye KNOCKED OUT" converted above its view count). Added to trend ranking rules.
- **Required loop-ending** (2026-06-25) — made the seamless-loop mandate REQUIRED (not optional), banned dead-stop endings. Validated by the 68s/123%-retention video; looping multiplies watch time (#1 algo signal). Targets the END of the retention curve.
- **Topic-ceiling / star-power ranking** (2026-06-25) — strict priority Ronaldo > Messi > Mbappé/Neymar/Haaland > A-listers > big national teams > rest. Every breakout has been Ronaldo; view ceiling = how many people care. Targets reach.
- **Plausibility guardrail** (2026-06-25) — shocking-but-TRUE only. Diagnosed from Jun24 flops: "Ronaldo's Career ENDS" (fabricated) got 1 view vs "Ronaldo's shocking collapse" (real) 1116. Fake/impossible claims flop AND risk the channel. Added to trend strategist.
- **Uploader token scope fix** — requests full union so upload runs don't strip analytics scopes.
- **datetime serialization fix** — publish_at normalized to RFC3339 string before upload.

---

## 🟡 QUEUED

1. **Batch-diversity guardrail** (found 2026-06-28) — the topic-ceiling bias over-concentrated:
   Jun28 batch was ALL 4 Ronaldo, with player_story + match_result near-DUPLICATES ("Ronaldo
   scores in SIXTH World Cup"). Need a rule so the 4 slots cover DIFFERENT players/stories within
   a batch (still big names, but spread — e.g. don't repeat the same news item across slots).
   Pair this with the Monday real-data work (real fixtures naturally diversify topics).

## ❌ CANCELLED (data-driven reversal)

- ~~**Tighten scripts 90-120 → 70-95 words**~~ — KILLED 2026-06-25. Clean length-vs-retention
  data (processed videos): 55s+ retains 54% vs <35s at 40%; longest videos (59-68s) retain
  60-123%; watch-time far higher on longer videos. Shortening would CUT watch time and HURT
  retention. Keep scripts at 90-120 words / 50-60s. Possibly test slightly LONGER later (see below).

---

## 🖐️ MANUAL (needs the user — not API-doable)

- **Profile picture** — sharp LogiSnap football logo (YouTube Studio → Customization).
- **Banner image** — football/WC banner w/ tagline "Daily World Cup 2026 Drama".

---

## 🔭 LATER / BIGGER

- **Auto-scheduling** — cron/launchd (local) or Oracle Cloud (24/7, Mac-independent) so daily batch runs without hitting the button. Deferred until traction justifies setup.
- **Post-tournament rebrand** (~July 19) — LogiSnap is a flexible multi-niche brand; swap WC branding when focus changes.
- **Monetization path** — need 1,000 subs for YPP (currently ~26). Real money = sponsorships + affiliates once audience is bigger, NOT Shorts RPM. Add affiliate links to descriptions once traffic justifies.
- **Multi-channel** — automation makes it scalable; clone the system to other leagues/sports once one channel is proven.

---

## 🧪 CANDIDATE EXPERIMENTS (test later, not queued yet)

- **Slightly LONGER scripts** — data shows 55-68s videos retain best (60-123%). Once the loop-ending
  change is in and measured, test nudging target from 50-60s → 58-65s. Cautious; one variable at a time.
- **More debate-format content** — debate slot is the early view leader (1,075 avg views). Once its
  retention processes, consider giving it a 2nd weekly slot or biasing other slots toward debate framing.

## 📌 WATCH-ITEMS (pending data, no action yet)

- **Debate slot** (NEW) — 1,075 avg views = highest of any type, retention still processing. Confirm it holds.
- **Fact slot** — still weakest on retention (~25-40%) even after hot-take upgrade; wins on views when spicy but doesn't hold. Watch whether hot-take facts close the gap.
- **Badge → subscriber conversion** — verdict still pending (API lag). So far no clear lift; subs look like a volume game (~0.1% conversion regardless).
- **Channel-page optimization → conversion** — measure once it's been live a few days.
- **Jun 21 soft day** (957 views, 40% ret) — one-off (Jun 22 recovered to 59.5%). Resolved unless it repeats.
