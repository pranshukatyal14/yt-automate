"""
Trend Researcher — Finds the best trending topic for a YouTube Short.

Flow:
  1. Gemini + Google Search grounding → fetch ~20 real-time trending topics
  2. Gemini    → act as senior content strategist, pick the single best topic
                 that will perform as a viral YouTube Short right now
  3. Returns   → (topic: str, style: str, rationale: str)

No extra API keys needed — uses the same Gemini key already in .env.
Google Search grounding is built into the Gemini API.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import httpx
from google import genai
from google.genai import types
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

_GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
_GROQ_MODEL   = "llama-3.3-70b-versatile"

logger = logging.getLogger(__name__)

# ── Gemini prompt ─────────────────────────────────────────────────────────────

_STRATEGIST_SYSTEM = """
You are a senior YouTube content strategist with 20 years of experience.
You have grown channels from 0 to millions of subscribers by picking the right
topics at the right time. You specialise in YouTube Shorts — 50-60 second vertical
videos for a mobile-first audience of ADULT MALE football fans aged 25-50 (76% of
this channel's viewers are men, core age 25-54). They GREW UP watching Ronaldo,
Messi and this golden generation — legacy, nostalgia and "the GOAT we grew up with"
angles hit them hardest. Audience is global: ~30% India, ~42% US/UK/Canada/Ireland
(USA is the most valuable + highest-retention segment).

You think in terms of:
- Virality: Does this topic have emotional charge (shock, awe, anger, inspiration)?
- Timing: Is this trending RIGHT NOW, or is it stale?
- Shorts-fit: Can this be told compellingly in 60 seconds?
- Broad appeal: Will this resonate across cultures, not just one niche?
- Re-watchability: Will people watch it twice because of a twist or a loop ending?

You always avoid: politics that polarise, explicit violence, clickbait with no payoff.

PLAUSIBILITY GUARDRAIL (data-proven 2026-06-25): shocking but TRUE/believable beats
fabricated. "Ronaldo's Career ENDS at World Cup 2026" got 1 view because it's obviously
false — viewers and the algorithm reject unbelievable claims, and false statements about
real people risk the channel. The topic must be a real, plausible angle on something that
actually happened or is genuinely being debated. Be provocative and spicy, NEVER invent
fake events or impossible outcomes. "Ronaldo's shocking collapse" (real, got 1116 views)
beats "Ronaldo's career ends" (fabricated, got 1 view) every time.
"""

_STRATEGIST_PROMPT = """
Here are the top trending searches right now on Google:

{trends_list}

Your job:
1. Identify the 3 best topics from this list that would make a VIRAL YouTube Short.
2. For each, briefly explain why it has viral potential for Shorts (1 sentence).
3. Pick your single WINNER — the one with the highest chance of exploding on Shorts today.
4. For the winner, write an optimised SHORT TITLE (≤92 chars, starts with emoji, uses power words).
5. Recommend the style: "story" for narrative/drama, "factual" for facts/tips.

RANKING RULES — topics score higher when they:
- Feature a star player by name (Messi, Ronaldo, Neymar, Mbappé, Vinicius Jr)
- **Carry NEGATIVE drama / controversy / failure about a superstar** — this is the
  #1 performance signal from our own data: "Ronaldo BLANKED vs DR Congo" did ~1000 views
  while celebratory "Messi scores" stories did 5–350. Pick the angle a fan would ARGUE about.
  Failure, red cards, chokes, benchings, feuds, criticism, "is he finished?" beat pure praise.
- Include an actual match result with a scoreline from TODAY
- Have emotional charge: shock, disbelief, controversy, record-breaking
- Are time-sensitive (happening RIGHT NOW beats yesterday's news)

ANGLE BIAS — when a topic CAN be framed as conflict/failure/controversy, frame it that way.
"Mbappé struggles as France stutter" beats "Mbappé plays for France". Take the spicy angle.

LEGACY/NOSTALGIA BIAS — the audience is men 25-50 who grew up with this golden generation.
"Is this the END of the Ronaldo/Messi era?", GOAT-legacy debates, and "the player we grew
up with" framing hit hardest. Lean into legacy stakes, not just today's news.

ELIMINATION/STAKES BIAS — high-stakes elimination moments convert viewers to SUBSCRIBERS
best (our data: "Turkiye KNOCKED OUT" drove subs above its view count). Prioritise
knockouts, shock exits, "X is OUT of the World Cup", do-or-die games, and tournament-defining
upsets — the bigger the stakes, the more people subscribe to follow what happens next.

TOPIC-CEILING / STAR-POWER RANKING — a video's view ceiling is capped by how many people
care about its subject. EVERY breakout this channel has had is a Ronaldo video. So when
choosing between topics of similar drama, ALWAYS pick the one with the biggest global star.
Strict priority order: Ronaldo > Messi > Mbappé/Neymar/Haaland > other A-list stars > big
national teams (Brazil/Argentina/England/France) > everyone else. A spicy take on Ronaldo
beats an equally spicy take on a mid-tier player every time — pick the bigger name.

US/WESTERN BIAS — ~42% of viewers are US/UK/Canada and the USA segment retains best and is
most valuable. When relevant, prioritise angles with global/US pull: the US Men's Team,
Christian Pulisic, USA upsets, or matchups Western audiences care about — alongside the
global superstars. Don't make it India-only.

Return ONLY a JSON object with exactly these keys:
  winner_topic    (str — the exact topic/angle for the script, written as a punchy title.
                   If a star player is involved, NAME THEM here),
  winner_title    (str — the YouTube-optimised title with emoji, ≤92 chars.
                   Put the player name or scoreline in the first 5 words if applicable),
  style           (str — "story" for emotional/dramatic player moments, "factual" for stats/facts),
  rationale       (str — one sentence on why this will go viral on Shorts today),
  top3            (array of 3 strings — the 3 best topics you considered).
"""


class TrendResearcher:
    """
    Researches trending topics and uses Gemini to pick the best for a YouTube Short.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str | None = None,
        niche: str | None = None,
        fmt: str | None = None,
    ) -> None:
        _key = api_key or os.environ["GEMINI_API_KEY"]
        self._model      = model_name or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self._niche      = (niche or os.getenv("CHANNEL_NICHE") or "").strip()
        self._fmt        = (fmt   or os.getenv("CHANNEL_FORMAT") or "").strip()
        self._groq_key   = os.getenv("GROQ_API_KEY")
        self._client     = genai.Client(api_key=_key)
        logger.info(
            "TrendResearcher initialised with model '%s' niche=%s format=%s",
            self._model, self._niche or "(none)", self._fmt or "(none)",
        )

    def research(self, video_type: str | None = None) -> dict[str, Any]:
        """
        Fetch trending topics and let Gemini pick the best for a viral Short.

        Parameters
        ----------
        video_type : optional — "player_story", "match_result", or "fact".
                     Focuses the search and ranking on that content type.

        Returns
        -------
        dict with keys:
          winner_topic  — the topic to pass to the scriptwriter
          winner_title  — SEO-optimised YouTube title suggestion
          style         — "story" or "factual"
          rationale     — why this topic will go viral
          top3          — the 3 topics Gemini considered
          raw_trends    — all trends fetched (for logging)
        """
        logger.info("Fetching trending topics from Google Trends… (type=%s)", video_type or "auto")
        try:
            trends = self._fetch_trends(video_type=video_type)
        except Exception as exc:
            logger.warning("Gemini trend fetch failed (%s) — using Groq fallback for trends", exc)
            trends = self._fetch_trends_groq(video_type=video_type)
        logger.info("Got %d trending topics", len(trends))

        if not trends:
            raise RuntimeError(
                "Could not fetch any trends. Check your internet connection."
            )

        try:
            result = self._pick_best(trends)
        except Exception as exc:
            logger.warning("Gemini _pick_best failed (%s) — using Groq fallback", exc)
            result = self._pick_best_groq(trends)
        result["raw_trends"] = trends

        logger.info(
            "Trend research complete → topic='%s' style=%s",
            result["winner_topic"],
            result["style"],
        )
        logger.info("Rationale: %s", result["rationale"])
        logger.info("Top 3 considered: %s", result["top3"])

        return result

    # ── Trend fetching ────────────────────────────────────────────────────────

    def _fetch_trends(self, video_type: str | None = None) -> list[str]:
        """
        Use Gemini with Google Search grounding to fetch ~20 real-time trending topics.
        Returns a list of trend strings.
        """
        if self._niche:
            fmt_clause = (
                f" Frame each topic as an instance of this recurring episode format: "
                f"\"{self._fmt}\"."
            ) if self._fmt else ""

            football_keywords = {"world cup", "fifa", "football", "soccer"}
            is_football = any(k in self._niche.lower() for k in football_keywords)
            if is_football:
                if video_type == "player_story":
                    football_clause = (
                        " FOCUS: Search specifically for the latest news, drama, controversy, "
                        "or viral moments involving individual star players — Lionel Messi, "
                        "Cristiano Ronaldo, Neymar Jr, Kylian Mbappé, Vinicius Jr, Erling Haaland. "
                        "ALSO consider viral football CREATORS/PERSONALITIES whose moments blow up — "
                        "e.g. IShowSpeed (and his Ronaldo obsession), other big football streamers, "
                        "and famous fan/celebrity reactions to World Cup moments. These names pull "
                        "huge search + algorithm traffic. NOTE: only build the story AROUND them as a "
                        "topic (named in the title) — never assume we have their footage. "
                        "What are fans saying TODAY? Any records, arguments, injuries, emotional "
                        "moments, viral reactions, or shocking performances?"
                    )
                elif video_type == "match_result":
                    football_clause = (
                        " FOCUS: Search specifically for today's and yesterday's FIFA World Cup 2026 "
                        "match results. Get the EXACT scores (e.g. 'France 3-1 Argentina'). "
                        "What was shocking or unexpected about the result? Any upsets, red cards, "
                        "last-minute goals, or VAR controversies? Include the scoreline in the topic."
                    )
                elif video_type == "debate":
                    football_clause = (
                        " FOCUS: Find the single most HEATED rivalry or debate in World Cup 2026 "
                        "football right now — Messi vs Ronaldo GOAT wars, 'who's the best player "
                        "at this World Cup', 'most overrated star', bold winner/flop predictions, "
                        "manager or tactical controversies, pundit hot-takes fans are fighting over. "
                        "Pick the topic that splits fans hardest and forces them to pick a side. "
                        "It MUST be an argument, not a fact — pure debate fuel."
                    )
                elif video_type == "fact":
                    football_clause = (
                        " FOCUS: Find the most DIVISIVE hot-take or debate in World Cup 2026 "
                        "football right now — the kind fans argue about in the comments. "
                        "Think: 'Messi is overrated and the numbers prove it', "
                        "'Ronaldo vs Messi — it's not even close', 'X is the most overrated team', "
                        "'Y doesn't deserve to start', 'Z is finished'. Frame it as a bold OPINION "
                        "or RANKING that takes a side and demands a reaction — NOT a neutral stat. "
                        "Controversy and debate drive comments and shares."
                    )
                else:
                    football_clause = (
                        " PRIORITY: Search for today's actual FIFA World Cup 2026 match results "
                        "(exact scores), any shocking player moments involving Messi, Ronaldo, "
                        "Neymar, Mbappé or other stars, controversial referee decisions, injuries, "
                        "or viral fan reactions from the last 24 hours."
                    )
            else:
                football_clause = ""

            search_prompt = (
                f"Search Google right now and give me a numbered list of the top 20 "
                f"trending stories, facts, news, debates, or viral moments related to "
                f"the niche: \"{self._niche}\". Focus on what people are searching, "
                f"discussing, or arguing about today. Each topic must clearly fit the "
                f"niche — do NOT include unrelated general trends.{football_clause}{fmt_clause} "
                f"Return ONLY a plain numbered list, one topic per line, no extra commentary."
            )
        else:
            search_prompt = (
                "Search Google right now and give me a numbered list of the top 20 trending "
                "topics, news events, and viral stories that people are searching for today. "
                "Include topics from entertainment, sports, technology, science, "
                "and world events. Return ONLY a plain numbered list, one topic per line, "
                "no extra commentary."
            )

        response = self._client.models.generate_content(
            model=self._model,
            contents=search_prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.0,
            ),
        )

        raw = response.text or ""
        trends: list[str] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            # Strip leading list markers like "1.", "1)", "-", "*"
            cleaned = re.sub(r"^[\d]+[.)]\s*|^[-*]\s*", "", line).strip()
            if cleaned:
                trends.append(cleaned)

        logger.info("Fetched %d trends via Gemini Search grounding", len(trends))
        return trends[:40]

    # ── Gemini strategy call ──────────────────────────────────────────────────

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=2, min=10, max=60),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _pick_best(self, trends: list[str]) -> dict[str, Any]:
        numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(trends))
        prompt   = _STRATEGIST_PROMPT.format(trends_list=numbered)
        if self._niche:
            prompt += (
                f"\n\nCHANNEL NICHE LOCK: This channel is ONLY about \"{self._niche}\". "
                f"The winner MUST clearly fit the niche. If no topic fits, pick the "
                f"closest adjacent one and reframe winner_topic as a niche-relevant angle "
                f"(e.g. niche='dark psychology' + trend='Olympics' → "
                f"'The dark psychology trick Olympic athletes use to never choke')."
            )
        if self._fmt:
            prompt += (
                f"\n\nSERIES FORMAT LOCK: Every episode on this channel follows the "
                f"format: \"{self._fmt}\". The winner_topic MUST be phrased as a specific "
                f"instance of that format — name the exact struggle/problem/scenario. "
                f"winner_title must also reflect the format so a viewer can instantly "
                f"recognise it as part of the series."
            )

        response = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=_STRATEGIST_SYSTEM,
                temperature=0.7,
                max_output_tokens=1024,
                response_mime_type="application/json",
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )

        parts = response.candidates[0].content.parts if response.candidates else []
        text_parts = [p.text for p in parts if hasattr(p, "text") and not getattr(p, "thought", False)]
        raw = ("".join(text_parts) if text_parts else response.text).strip()
        clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.DOTALL).strip()
        result = json.loads(clean)

        required = {"winner_topic", "winner_title", "style", "rationale", "top3"}
        missing  = required - result.keys()
        if missing:
            raise ValueError(f"Gemini trend response missing keys: {missing}")

        return result

    def _groq_chat(self, system: str, user: str) -> str:
        if not self._groq_key:
            raise RuntimeError("GROQ_API_KEY not set — cannot use Groq fallback")
        resp = httpx.post(
            _GROQ_API_URL,
            headers={"Authorization": f"Bearer {self._groq_key}", "Content-Type": "application/json"},
            json={
                "model": _GROQ_MODEL,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "temperature": 0.7,
                "max_tokens": 1024,
            },
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    def _fetch_trends_groq(self, video_type: str | None = None) -> list[str]:
        """Groq fallback for trend fetching — uses its training knowledge, no live search."""
        logger.warning("Using Groq for trend fetch (no live search — based on training data)")
        niche_clause = f'for the niche: "{self._niche}"' if self._niche else "across entertainment, sports, and news"
        type_clause = {
            "player_story": "Focus on star football players: Messi, Ronaldo, Neymar, Mbappé, Vinicius Jr.",
            "debate":       "Focus on a HEATED rivalry/debate fans fight over — Messi vs Ronaldo GOAT wars, 'best/most overrated player at the World Cup', bold winner/flop predictions, tactical controversies. Must be an argument that splits fans, NOT a fact.",
            "match_result": "Focus on recent FIFA World Cup 2026 match results and scorelines.",
            "fact":         "Focus on a DIVISIVE hot-take/debate fans argue about — e.g. 'Messi is overrated', 'Ronaldo is finished', 'X vs Y, not even close'. A bold opinion or ranking that takes a side, NOT a neutral stat.",
        }.get(video_type or "", "")
        prompt = (
            f"Give me a numbered list of 20 highly viral and trending topics right now {niche_clause}. "
            f"{type_clause} Return ONLY a plain numbered list, one topic per line, no commentary."
        )
        raw = self._groq_chat(_STRATEGIST_SYSTEM, prompt)
        trends = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            cleaned = re.sub(r"^[\d]+[.)]\s*|^[-*]\s*", "", line).strip()
            if cleaned:
                trends.append(cleaned)
        logger.info("Groq trend fetch returned %d topics", len(trends))
        return trends[:40]

    def _pick_best_groq(self, trends: list[str]) -> dict[str, Any]:
        """Groq fallback for topic selection — returns same JSON schema as Gemini."""
        numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(trends))
        prompt = _STRATEGIST_PROMPT.format(trends_list=numbered)
        if self._niche:
            prompt += f'\n\nCHANNEL NICHE LOCK: "{self._niche}". Winner MUST fit the niche.'
        prompt += '\n\nReturn ONLY valid JSON with keys: winner_topic, winner_title, style, rationale, top3.'
        raw = self._groq_chat(_STRATEGIST_SYSTEM, prompt)
        clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.DOTALL).strip()
        result = json.loads(clean)
        required = {"winner_topic", "winner_title", "style", "rationale", "top3"}
        missing = required - result.keys()
        if missing:
            raise ValueError(f"Groq trend response missing keys: {missing}")
        logger.info("Groq _pick_best → topic='%s'", result["winner_topic"])
        return result
