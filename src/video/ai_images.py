"""
Free AI-generated football scene images via Pollinations.ai (no key, no limit).

Why: generic Pexels stock often doesn't match the drama of the topic. These
cinematic, emotionally-charged football scenes are more striking and on-brand,
which lifts retention. Mixed INTO the Pexels pool (not replacing it).

Hard rule learned from testing: NEVER put real player names in the prompt — the
model renders them as surreal garbage (a lion-headed footballer). Only generic
dramatic football scenes. Every call hard-falls-back to [] so it can't break a run.
"""
from __future__ import annotations

import logging
import subprocess
import time
import urllib.parse
from pathlib import Path

logger = logging.getLogger(__name__)

_BASE = "https://image.pollinations.ai/prompt/"

# Curated cinematic football scenes (NO player names). Cover the emotional range
# of World Cup drama: triumph, despair, tension, spectacle.
_SCENE_PROMPTS = [
    "professional footballer screaming in celebration, floodlit stadium at night, cinematic, photorealistic, dramatic rim lighting, confetti",
    "dejected footballer kneeling alone on the pitch, defeat, dramatic stadium floodlights, mist, cinematic, photorealistic",
    "intense football match action, two players battling for the ball, packed roaring stadium, cinematic, photorealistic, motion blur",
    "huge football crowd packed stadium at night, flares smoke and flags, electric atmosphere, cinematic, photorealistic",
    "referee holding up a red card, tense dramatic moment, stadium floodlights, cinematic, photorealistic, shallow depth of field",
    "golden world cup trophy on the pitch under a dramatic spotlight, empty stadium, cinematic, photorealistic",
    "lone footballer silhouette walking off the pitch, dramatic floodlights and long shadow, emotional, cinematic, photorealistic",
    "goalkeeper diving full stretch to save a penalty, dramatic floodlit stadium, cinematic, photorealistic, frozen motion",
]


def generate_scene_images(out_dir: str | Path, n: int = 4, seed_base: int = 0) -> list[Path]:
    """
    Generate `n` cinematic football scene images. Returns local file paths, or []
    on any failure (→ caller falls back to stock footage).
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    # Pick n distinct scenes, cycling if n > pool
    chosen = [_SCENE_PROMPTS[(seed_base + i) % len(_SCENE_PROMPTS)] for i in range(n)]
    for i, prompt in enumerate(chosen):
        try:
            q = urllib.parse.quote(prompt)
            seed = (seed_base * 31 + i * 7 + 1) % 100000
            url = f"{_BASE}{q}?width=1080&height=1920&nologo=true&model=flux&seed={seed}"
            dest = out / f"ai_scene_{seed}.jpg"
            res = subprocess.run(
                ["curl", "-s", "--max-time", "45", "-o", str(dest), url],
                capture_output=True, timeout=50,
            )
            if res.returncode == 0 and dest.exists() and dest.stat().st_size > 5000:
                paths.append(dest)
            else:
                logger.warning("AI image %d skipped (rc=%s size=%s)", i,
                               res.returncode, dest.stat().st_size if dest.exists() else 0)
        except Exception as exc:
            logger.warning("AI image generation failed (%s)", str(exc)[:90])
        time.sleep(0.6)   # space out requests — Pollinations throttles bursts
    if paths:
        logger.info("AI scene images generated: %d/%d", len(paths), n)
    return paths
