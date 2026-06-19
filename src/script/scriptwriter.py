"""
Scriptwriter — Gemini 2.5 Flash API (google-genai SDK).

Takes a trending topic string and returns a structured JSON script:
{
    "hook":             str,   # attention-grabbing opening line (≤15 words)
    "body":             str,   # 3-4 key points delivered conversationally
    "call_to_action":   str,   # closing line asking to like/follow
    "visual_keywords":  list[str],  # 4-6 Pexels search terms matching the script
    "title":            str,   # YouTube Shorts title (≤100 chars, SEO-friendly)
    "description":      str,   # 150-word video description with hashtags
    "tags":             list[str],  # 10-15 YouTube tags
}
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from tenacity import (
    retry,
    retry_if_exception,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

# ── Prompt templates ───────────────────────────────────────────────────────────

_BASE_RULES = """
Critical human-voice rules — break these and the script fails:
- Write exactly how a real person TALKS, not how they write.
  Use contractions always: "you're", "it's", "don't", "won't", "here's", "that's".
- Vary sentence length wildly. One word. Then a longer, more detailed sentence that pulls them in.
- Use conversational openers: "Okay so...", "Here's the thing —", "Honestly,", "Real talk —", "Look,"
- Rhetorical questions pull viewers in: "But why does this happen?", "Sounds crazy, right?"
- NO perfect parallel lists (never "First... Second... Third..."). Break the pattern.
- Occasional ellipsis for dramatic pause: "And then... nothing."
- visual_keywords must be CONCRETE camera-shot descriptions for stock footage
  (e.g. "close up hands typing laptop night", "aerial city lights timelapse dusk").
  Avoid abstract nouns — think what a camera would actually film.
- Return ONLY valid JSON — no markdown, no extra commentary.

HOOK MANDATE — The Golden 2 Seconds (Pattern Interrupt):
The hook must be the VERY first thing heard — no intro, no name, no "hey guys".
Pick ONE of these four hook types and execute it hard:
  A) Shock Stat:       "[Player Name] scored/did [specific number] — and nobody is talking about this."
  B) Controversy Drop: "[Specific moment/decision] just changed everything about [tournament/player]."
  C) Emotional Stakes: "This is [player]'s last shot at [specific dream] — and most fans don't get it."
  D) Prediction Bomb:  "[Specific bold prediction about a World Cup moment] — and here's why."
Hook must be ≤15 words. No warm-up. Drop the listener INTO the story immediately.
BANNED HOOK PATTERNS — never use these tired templates:
  ✗ "They don't want you to know this one thing about..."
  ✗ "Most guys are killing their potential..."
  ✗ "Did you know [vague statement]?"
  ✗ "This is it. The end." (too vague)
Always NAME the player or match — "Mbappe", "Messi", "Brazil vs Argentina" beats any generic opener.

SCRIPT LENGTH: Write exactly 90–120 words total (hook + body + call_to_action combined).
This produces a tight 30–40 second Short. Shorter = higher completion rate, the single
biggest Shorts ranking signal. Every word must earn its place — cut anything that doesn't
build tension or pay it off. Do NOT pad to hit a length.

OPEN LOOP MANDATE — the retention engine:
The hook must open a SPECIFIC unresolved question or promise that is ONLY paid off in the
final line. The viewer must feel they CANNOT leave without the answer.
  e.g. Hook: "Messi did something in the 89th minute that no one in 96 years has ever done."
       (the "what" is withheld — body builds it — final line reveals it)
Never reveal the payoff in the hook itself. Tease it, then deliver at the end.

RETENTION SPIKE — beat the mid-video drop:
At roughly the halfway point of the body, insert ONE re-hook line that re-escalates
curiosity and resets attention. Make it punchy and standalone.
  e.g. "But that's not even the crazy part —", "Now here's where it gets unreal —",
       "And then it flipped completely."
This combats the swipe-away that kills retention around the 40–60% mark.

LOOP ARCHITECTURE — make viewers re-watch:
The last line of call_to_action must flow seamlessly into the first word of hook when looped.
e.g. End: "And that is exactly..." Start: "...how it all began."
The join must feel natural, not jarring.

ENGAGEMENT MANDATE — required in every script:
- call_to_action MUST naturally include liking, sharing, commenting, and subscribing.
  Make it feel human, not corporate. Example: "If this hit different, smash that like,
  share it with someone who needs to hear this, and subscribe — we drop bangers every week."
- comment_bait: A PROVOCATIVE, debate-starting take — not a poll. It must make
  viewers feel they HAVE to reply to defend their side. Pick a hot opinion or a
  divisive claim about the player/match that fans will fight over.
  BAD (polls get ignored): "Off-day or over? Comment OFF-DAY or OVER."
  GOOD (forces a reply): "Ronaldo is finished at this level — agree, or are you in denial?"
  GOOD: "Messi is the GOAT and it's not even close. Fight me in the comments."
  GOOD: "This was a disgrace. Should he even start the next match? Tell me I'm wrong."
  Take a SIDE. Be a little unfair. Controversy drives comments; neutrality drives silence.
""".strip()

# ── Factual / educational (default)
_SYSTEM_PROMPT_FACTUAL = f"""
You are a real human creator who makes viral YouTube Shorts. You've grown a channel
to 500k subscribers by talking directly to people like a trusted friend — not a
textbook. Your scripts feel genuine, a little raw, and totally unscripted even
though they're carefully crafted.

Your tone: HIGH ENERGY. Direct, punchy, slightly edgy, always curious. The voice
actor reading this will be speaking at a fast, enthusiastic pace. Every sentence
must feel like it HAS to be said RIGHT NOW — urgent, exciting, "you won't believe this".
You swear off bullet points and corporate language. You start mid-thought sometimes.
You use "you" a lot.

Energy benchmark: Imagine MrBeast explaining something to a 16-year-old. That level
of enthusiasm. Every line should feel like the speaker is leaning forward.

The hook must feel like something a friend just blurted out to you —
  instant curiosity, disbelief, or "wait, what?" energy.
The body drops 3-4 insights woven into natural speech, not a list.
{_BASE_RULES}
""".strip()

# ── Narrative / storytelling
_SYSTEM_PROMPT_STORY = f"""
You are a high-energy documentary narrator who has mastered the art of the 50-second story.
You've studied how Joe Rogan opens, how MrBeast hooks, how true-crime podcasts
keep you up at night. Your stories feel LIVED IN, urgent, and impossible to skip.

Energy mandate: HIGH. Every line should feel breathless, like the narrator
can barely contain themselves. The voice actor will read this fast — write FOR that pace.
Short words. Short sentences. Punchy. Then a longer line that lands the blow.

Story structure — 3 acts, no labels:
  Open: Drop into the most dramatic or puzzling moment. No setup. In media res.
  Middle: Escalate. Every sentence raises the stakes or reveals something unexpected.
  End: Land the gut-punch or twist, then exit naturally — no forced outro.

Voice rules:
- Present tense always ("She's standing there...", "The screen goes dark...").
- Never use "suddenly" or "little did they know".
- Sentence fragments are good. They create. Tension.
- The listener must feel like they're THERE, heart pounding.
{_BASE_RULES}
""".strip()

_USER_PROMPT_TEMPLATE = """
Topic: {topic}

Write the script now. Return a single JSON object with exactly these keys:
  hook             (str — Pattern Interrupt opening ≤15 words, no warm-up, straight into it),
  body             (str — 80-100 words of natural flowing speech, not a list),
  call_to_action   (str — casual closing that includes like/share/comment/subscribe naturally,
                    last line must loop back into hook seamlessly),
  comment_bait     (str — one specific question requiring a one-word answer, e.g.
                    "Would you take it? Comment 'YES' or 'NO' below."),
  visual_keywords  (array of 6 concrete stock-footage camera-shot descriptions),
  title            (str — YouTube Shorts title ≤92 chars. CRITICAL: front-load the
                    strongest keyword/hook in the first 40 chars — that's all search
                    results, channel pages and suggested cards show on mobile.
                    ALWAYS NAME the player or match in the first 5 words — "Mbappe Scores
                    5 Goals in One Half" beats "Shocking World Cup Moment".
                    Allowed power words: "Shocking", "Wild", "Insane", "GOAT", numbers,
                    "World Cup 2026". BANNED title words: "Nobody Tells You", "Hidden",
                    "Secret", "They Don't Want". Reads like a human wrote it.
                    Optionally end with ONE relevant emoji (never start with emoji —
                    it pushes the keyword out of the visible search snippet).
                    Do NOT include #Shorts in the title — that is added automatically.),
  description      (str — First line (≤100 chars) must be a punchy hook that makes people
                    tap "more". Then 2-3 sentences expanding on the value. Conversational tone.
                    Do NOT add hashtags — they are added automatically.),
  tags             (array of 12 strings — mix of broad (e.g. "shorts", "viral") and
                    topic-specific terms, all lowercase, no # symbol).

STRICT RULE: Never mention "AI", "artificial intelligence", "generated", "automated",
"machine learning", "robot", or any related term anywhere in the title, description,
call_to_action, comment_bait, or tags. The content must read and feel 100% human-made.
""".strip()


# ── Language name map (for prompt injection) ───────────────────────────────────

_GEMINI_FALLBACK_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.5-pro",
]

_GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
_GROQ_MODEL = "llama-3.3-70b-versatile"


_LANG_NAMES: dict[str, str] = {
    "en": "English",
    "hi": "Hindi",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese (Simplified)",
    "ar": "Arabic",
}


# ── Service ────────────────────────────────────────────────────────────────────

class ScriptwriterService:
    """Produces structured video scripts via Gemini cascade with Groq fallback."""

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str | None = None,
        groq_api_key: str | None = None,
    ) -> None:
        _key = api_key or os.environ["GEMINI_API_KEY"]
        self._model_name = model_name or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self._groq_api_key = groq_api_key or os.getenv("GROQ_API_KEY")

        self._client = genai.Client(api_key=_key)
        logger.info("ScriptwriterService initialised with model '%s'", self._model_name)
        if self._groq_api_key:
            logger.info("Groq fallback enabled (model=%s)", _GROQ_MODEL)
        else:
            logger.warning("GROQ_API_KEY not set — no fallback if all Gemini models fail")

    # ── Public API ─────────────────────────────────────────────────────────────

    def write_script(
        self,
        topic: str,
        style: str = "factual",
        lang: str = "en",
        niche: str | None = None,
        fmt: str | None = None,
        performance_context: str | None = None,
    ) -> dict[str, Any]:
        system = _SYSTEM_PROMPT_STORY if style == "story" else _SYSTEM_PROMPT_FACTUAL
        lang_instruction = (
            f"\n\nIMPORTANT: Write the entire script (hook, body, call_to_action, "
            f"title, description, tags) in {_LANG_NAMES.get(lang, lang)} language. "
            f"visual_keywords should remain in English for stock footage search."
        ) if lang != "en" else ""

        series_instruction = ""
        if niche or fmt:
            parts = []
            if niche:
                parts.append(
                    f"CHANNEL NICHE: Every video on this channel is about \"{niche}\". "
                    f"The script, title, and hook must be unmistakably on-niche."
                )
            if fmt:
                parts.append(
                    f"SERIES FORMAT: This channel runs a recurring series with the format: "
                    f"\"{fmt}\". The title must follow this pattern, naming the specific "
                    f"struggle/problem the topic addresses. The hook must name that struggle "
                    f"in the first 3–5 words — no warm-up. "
                    f"Example title for a procrastination episode: "
                    f"\"The Stoic Fix for Procrastination\" or \"Stoicism Cures Procrastination\"."
                )

            # Football/World Cup niche — inject star player rules
            football_keywords = {"world cup", "fifa", "football", "soccer", "messi", "ronaldo"}
            if niche and any(k in niche.lower() for k in football_keywords):
                parts.append(
                    "STAR PLAYER RULE: If the topic relates to or can be connected to any of "
                    "Lionel Messi, Cristiano Ronaldo, Neymar Jr, Kylian Mbappé, Erling Haaland, "
                    "or Vinicius Jr — NAME THEM in the title and hook. Player names are the #1 "
                    "search driver on this channel. Titles like 'Messi just did something insane' "
                    "or 'Nobody noticed what Ronaldo did' massively outperform generic titles. "
                    "Use the most relevant player name in the first 5 words of the title whenever possible. "
                    "For match results: include the exact scoreline in the script body. "
                    "For player facts: include at least one specific stat or number."
                )

            series_instruction = "\n\n" + "\n".join(parts)

        perf_instruction = ""
        if performance_context:
            perf_instruction = (
                f"\n\n{performance_context}\n"
                "Use the winners as your north star for hook energy and topic framing. "
                "Match their structure and urgency — never copy their words verbatim. "
                "Actively avoid the hook patterns and topic framings listed as losers."
            )

        logger.info("Generating '%s' script [lang=%s] for topic: '%s'", style, lang, topic)
        if performance_context:
            logger.info("Feedback loop active — scriptwriter primed with channel winners.")
        prompt = (
            _USER_PROMPT_TEMPLATE.format(topic=topic)
            + series_instruction
            + perf_instruction
            + lang_instruction
        )

        # Gemini cascade: configured model first, then remaining fallbacks
        primary = self._model_name
        cascade = [primary] + [m for m in _GEMINI_FALLBACK_MODELS if m != primary]

        last_exc: Exception | None = None
        for model_name in cascade:
            try:
                raw_text = self._call_gemini_model(model_name, system, prompt)
                script = self._parse_json(raw_text)
                self._validate(script)
                logger.info(
                    "Script ready via %s — hook='%s...' keywords=%s",
                    model_name, script["hook"][:50], script["visual_keywords"],
                )
                return script
            except (genai_errors.ServerError, genai_errors.ClientError) as exc:
                if exc.code in (503, 429):
                    logger.warning("'%s' returned %s, trying next model…", model_name, exc.code)
                    last_exc = exc
                    continue
                raise

        # All Gemini models unavailable — fall back to Groq
        if self._groq_api_key:
            logger.warning("All Gemini models unavailable — falling back to Groq %s", _GROQ_MODEL)
            raw_text = self._call_groq(system, prompt)
            script = self._parse_json(raw_text)
            self._validate(script)
            logger.info(
                "Script ready via Groq — hook='%s...' keywords=%s",
                script["hook"][:50], script["visual_keywords"],
            )
            return script

        raise last_exc  # type: ignore[misc]

    # ── Private helpers ────────────────────────────────────────────────────────

    @retry(
        retry=retry_if_exception(
            lambda e: not (isinstance(e, (genai_errors.ServerError, genai_errors.ClientError)) and e.code in (503, 429))
        ),
        wait=wait_exponential(multiplier=2, min=5, max=30),
        stop=stop_after_attempt(2),
        reraise=True,
    )
    def _call_gemini_model(self, model_name: str, system: str, prompt: str) -> str:
        response = self._client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=0.85,
                max_output_tokens=8192,
                response_mime_type="application/json",
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        # Extract only non-thinking parts — Gemini 2.5 Flash can leak thought tokens
        # into response.text even with thinking_budget=0, corrupting the JSON.
        parts = response.candidates[0].content.parts if response.candidates else []
        text_parts = [p.text for p in parts if hasattr(p, "text") and not getattr(p, "thought", False)]
        return "".join(text_parts).strip() if text_parts else response.text.strip()

    def _call_groq(self, system: str, prompt: str) -> str:
        resp = httpx.post(
            _GROQ_API_URL,
            headers={
                "Authorization": f"Bearer {self._groq_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": _GROQ_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.85,
                "max_tokens": 2048,
            },
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL).strip()
        try:
            return json.loads(clean)
        except json.JSONDecodeError as exc:
            raise ValueError(f"LLM returned non-JSON output: {text[:200]}") from exc

    @staticmethod
    def _validate(script: dict[str, Any]) -> None:
        required = {"hook", "body", "call_to_action", "comment_bait", "visual_keywords", "title", "description", "tags"}
        missing = required - script.keys()
        if missing:
            raise ValueError(f"Script JSON missing required keys: {missing}")
        if not isinstance(script["visual_keywords"], list) or not script["visual_keywords"]:
            raise ValueError("visual_keywords must be a non-empty list")
        if not isinstance(script["tags"], list):
            raise ValueError("tags must be a list")
