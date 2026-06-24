# LogiSnap — Action Items & Backlog

> Living list of what's shipped, what's queued, and why. Update as things move.
> Last updated: 2026-06-24

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
- **Uploader token scope fix** — requests full union so upload runs don't strip analytics scopes.
- **datetime serialization fix** — publish_at normalized to RFC3339 string before upload.

---

## 🟡 QUEUED — ship when in-flight experiments report back (~when API processes Jun 22-24)

These were deliberately held to keep current experiments clean (debate slot, geo-timing, channel page, audience re-tune):

1. **Tighten scripts further** — 90-120 → 70-95 words (~25-32s) for higher completion → target 60% retention.
2. **Make seamless-loop mandate REQUIRED** (not optional) — re-watch multiplies reach (MY/ID already loop at 130%+).
3. **Topic-ceiling bias** — prioritise biggest global names (Ronaldo > Messi > Mbappé/Neymar > rest). View ceiling = how many people care; every breakout so far is Ronaldo.
   - (Was committed a771b67, reverted fe1c5bb to avoid muddying experiments.)

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

## 📌 WATCH-ITEMS (pending data, no action yet)

- **Badge → subscriber conversion** — verdict still pending (API lag). So far no clear lift; subs look like a volume game (~0.1% conversion regardless).
- **Channel-page optimization → conversion** — measure once it's been live a few days.
- **Jun 21 soft day** (957 views, 40% ret) — one-off or trend? Jun 22 recovered. Watch.
