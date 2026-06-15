"""
Thumbnail generator — PIL-rendered 1280x720 cover frames for Shorts.

YouTube Shorts thumbnails are square-cropped (~720x720) on mobile, so the
composition keeps the focal text in the center and uses bold colour bands
on top/bottom for impact at small sizes.

Output: JPEG, ≤2MB (YouTube API limit).
"""

from __future__ import annotations

import logging
import os
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

logger = logging.getLogger(__name__)

# YouTube spec: thumbnails up to 1280x720, ≤2MB JPEG.
W, H = 1280, 720

# High-contrast accent palette — picked at random per render so the channel
# doesn't look monotonous, but each individual thumbnail stays bold.
_ACCENT_PALETTE: list[tuple[int, int, int]] = [
    (255, 64, 64),     # red
    (255, 196, 0),     # yellow
    (0, 200, 255),     # cyan
    (255, 0, 196),     # magenta
    (32, 255, 128),    # green
    (255, 128, 0),     # orange
]

_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/HelveticaNeue.ttc",
]


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        test = (cur + " " + w).strip()
        if draw.textlength(test, font=font) > max_width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = test
    if cur:
        lines.append(cur)
    return lines


def _shorten_for_thumb(title: str, max_words: int = 6) -> str:
    """Strip leading emoji + trailing #Shorts; keep at most max_words for impact."""
    cleaned = title.strip()
    # Drop a leading emoji + space if present (single grapheme heuristic)
    if cleaned and not cleaned[0].isalnum() and not cleaned[0].isspace():
        cleaned = cleaned.split(" ", 1)[-1] if " " in cleaned else cleaned
    cleaned = cleaned.replace("#Shorts", "").replace("#shorts", "").strip()
    words = cleaned.split()
    if len(words) > max_words:
        cleaned = " ".join(words[:max_words]) + "…"
    return cleaned.upper()


def generate_thumbnail(
    title: str,
    output_path: str | Path,
    background_image: str | Path | None = None,
    accent_color: tuple[int, int, int] | None = None,
) -> Path:
    """
    Render a 1280x720 thumbnail.

    Layout:
      - Optional blurred background image (e.g. a frame from the rendered video).
      - Dark vignette/overlay for legibility.
      - Top accent band (~60px) with the niche/channel label colour.
      - Centre: huge stacked title text, white with black stroke + drop shadow.
      - Bottom accent band (~40px) for visual symmetry.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    accent = accent_color or random.choice(_ACCENT_PALETTE)

    # ── Background ─────────────────────────────────────────────────────────────
    if background_image and Path(background_image).exists():
        try:
            bg = Image.open(background_image).convert("RGB")
            bg = bg.resize((W, H), Image.LANCZOS)
            bg = bg.filter(ImageFilter.GaussianBlur(radius=14))
        except Exception as exc:
            logger.warning("Could not load background image: %s — falling back", exc)
            bg = Image.new("RGB", (W, H), (12, 12, 16))
    else:
        bg = Image.new("RGB", (W, H), (12, 12, 16))

    # Dark overlay for text legibility
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 130))
    img = bg.convert("RGBA")
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)

    # ── Accent bands ───────────────────────────────────────────────────────────
    draw.rectangle([0, 0, W, 60], fill=accent + (255,))
    draw.rectangle([0, H - 40, W, H], fill=accent + (255,))

    # ── Title text ─────────────────────────────────────────────────────────────
    short_title = _shorten_for_thumb(title)

    # Auto-fit font size to width
    size = 180
    font = _load_font(size)
    while size > 80:
        font = _load_font(size)
        lines = _wrap(draw, short_title, font, max_width=W - 160)
        max_line_w = max((draw.textlength(ln, font=font) for ln in lines), default=0)
        total_h = size * len(lines) * 1.05
        if max_line_w <= W - 160 and total_h <= H - 200:
            break
        size -= 10

    line_h = int(size * 1.05)
    total_h = line_h * len(lines)
    y = (H - total_h) // 2

    for line in lines:
        line_w = draw.textlength(line, font=font)
        x = (W - line_w) // 2
        # Drop shadow
        draw.text((x + 6, y + 6), line, font=font, fill=(0, 0, 0, 200))
        # Stroked main text
        draw.text(
            (x, y), line, font=font,
            fill=(255, 255, 255, 255),
            stroke_width=6,
            stroke_fill=(0, 0, 0, 255),
        )
        y += line_h

    # ── Save (JPEG, quality 88 keeps it well under 2MB) ────────────────────────
    img.convert("RGB").save(output_path, "JPEG", quality=88, optimize=True)
    logger.info("Thumbnail saved: %s (accent=%s)", output_path, accent)
    return output_path


def extract_frame(
    video_path: str | Path,
    timestamp: float,
    output_path: str | Path,
) -> Path | None:
    """
    Pull a single frame from a video file at *timestamp* seconds.
    Used as the blurred background for the thumbnail. Returns None on failure.
    """
    try:
        from moviepy import VideoFileClip
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with VideoFileClip(str(video_path)) as clip:
            t = min(max(timestamp, 0.0), max(clip.duration - 0.1, 0.0))
            clip.save_frame(str(output_path), t=t)
        return output_path
    except Exception as exc:
        logger.warning("Could not extract frame from %s: %s", video_path, exc)
        return None
