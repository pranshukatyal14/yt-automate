"""
Video Editor — moviepy + Pexels stock footage.

Pipeline:
  1. Fetch 1080×1920 HD portrait clips from Pexels using visual_keywords.
  2. Apply Ken Burns zoom animation to each clip.
  3. Concatenate clips to match audio length.
  4. Composite the voiceover audio over the video.
  5. Overlay PIL-rendered language-aware TikTok-style captions with fade-in.
  6. Render final MP4 at 1080×1920 (9:16) @ 30 fps.
"""

from __future__ import annotations

import logging
import math
import os
import random
from pathlib import Path

import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFont
from moviepy import (
    AudioFileClip,
    ColorClip,
    CompositeVideoClip,
    ImageClip,
    VideoFileClip,
    afx,
    concatenate_videoclips,
    vfx,
)

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────

W   = int(os.getenv("VIDEO_RESOLUTION_W", 1080))
H   = int(os.getenv("VIDEO_RESOLUTION_H", 1920))
FPS = int(os.getenv("VIDEO_FPS", 30))

PEXELS_VIDEO_API  = "https://api.pexels.com/videos/search"
PIXABAY_VIDEO_API = "https://pixabay.com/api/videos/"

# ── Language-aware font map ────────────────────────────────────────────────────
# Each entry: [preferred, fallback1, fallback2, ...]
_FONT_MAP: dict[str, list[str]] = {
    # Latin (default — Impact for viral look)
    "default": [
        "/System/Library/Fonts/Supplemental/Impact.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/HelveticaNeue.ttc",
    ],
    # Hindi / Devanagari
    "hi": [
        "/System/Library/Fonts/Devanagari Sangam MN.ttc",
        "/System/Library/Fonts/ITFDevanagari.ttc",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ],
    # Arabic (RTL — PIL handles via bidi if text is shaped; basic rendering works)
    "ar": [
        "/System/Library/Fonts/SFArabic.ttf",
        "/System/Library/Fonts/SFArabicRounded.ttf",
    ],
    # Japanese / Chinese (shared CJK fonts)
    "ja": [
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    ],
    "zh": [
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
    ],
    # Korean
    "ko": [
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "/System/Library/Fonts/AppleGothic.ttf",
    ],
}


# ── Pexels footage fetcher ─────────────────────────────────────────────────────

class PexelsFetcher:
    """Searches Pexels for HD portrait stock video clips and downloads them."""

    def __init__(self, api_key: str | None = None) -> None:
        self._key = api_key or os.environ["PEXELS_API_KEY"]
        self._session = requests.Session()
        self._session.headers.update({"Authorization": self._key})

    def search(self, query: str, per_page: int = 5) -> list[str]:
        """Return direct MP4 URLs for *query*, best portrait quality first."""
        params = {
            "query":       query,
            "orientation": "portrait",
            "size":        "medium",
            "per_page":    per_page,
        }
        resp = self._session.get(PEXELS_VIDEO_API, params=params, timeout=15)
        resp.raise_for_status()
        videos = resp.json().get("videos", [])
        if not videos:
            logger.warning("Pexels: 0 results for '%s'", query)
            return []

        return [url for v in videos if (url := self._pick_best_file(v.get("video_files", [])))]

    @staticmethod
    def _pick_best_file(files: list[dict]) -> str | None:
        def is_portrait(f: dict) -> bool:
            return f.get("height", 0) > f.get("width", 0)
        def res(f: dict) -> int:
            return f.get("height", 0) * f.get("width", 0)

        tier1 = [f for f in files if f.get("width") == 1080 and f.get("height") == 1920]
        tier2 = [f for f in files if f.get("quality") == "hd" and is_portrait(f)]
        tier3 = [f for f in files if is_portrait(f) and f.get("width", 0) >= 360]
        for tier in (tier1, tier2, tier3, files):
            if tier:
                return max(tier, key=res).get("link")
        return None

    def download(self, url: str, dest_dir: Path) -> Path:
        """Stream-download *url* to *dest_dir*; return local path."""
        dest_dir.mkdir(parents=True, exist_ok=True)
        fname = url.split("?")[0].split("/")[-1]
        if not fname.endswith(".mp4"):
            fname += ".mp4"
        dest = dest_dir / fname
        if dest.exists():
            return dest

        logger.info("Downloading %s → %s", url[:80], dest.name)
        with self._session.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
        return dest


# ── Pixabay footage fetcher ────────────────────────────────────────────────────

class PixabayFetcher:
    """Pixabay video search — free tier (200 req/min). Portrait/vertical clips."""

    def __init__(self, api_key: str | None = None) -> None:
        self._key = api_key or os.getenv("PIXABAY_API_KEY")
        self._session = requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self._key)

    def search(self, query: str, per_page: int = 5) -> list[str]:
        """Return direct MP4 URLs for *query*, portrait-preferred."""
        if not self._key:
            return []
        params = {
            "key":      self._key,
            "q":        query,
            "video_type": "film",
            "per_page": max(per_page, 3),      # Pixabay minimum is 3
            "safesearch": "true",
        }
        try:
            resp = self._session.get(PIXABAY_VIDEO_API, params=params, timeout=15)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Pixabay request failed for '%s': %s", query, exc)
            return []

        hits = resp.json().get("hits", [])
        if not hits:
            logger.warning("Pixabay: 0 results for '%s'", query)
            return []

        urls: list[str] = []
        for hit in hits:
            url = self._pick_best_file(hit.get("videos", {}))
            if url:
                urls.append(url)
        return urls

    @staticmethod
    def _pick_best_file(videos: dict) -> str | None:
        """Pixabay returns dict of size-name → {url, width, height, size}. Prefer portrait."""
        candidates = [v for v in videos.values() if v.get("url")]
        if not candidates:
            return None

        def is_portrait(v: dict) -> bool:
            return v.get("height", 0) > v.get("width", 0)
        def res(v: dict) -> int:
            return v.get("height", 0) * v.get("width", 0)

        portrait = [v for v in candidates if is_portrait(v) and v.get("width", 0) >= 720]
        if portrait:
            return max(portrait, key=res)["url"]
        # Fall back to any HD clip — _build_background will center-crop to 9:16
        hd = [v for v in candidates if v.get("width", 0) >= 1280]
        return (max(hd, key=res) if hd else max(candidates, key=res))["url"]

    def download(self, url: str, dest_dir: Path) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        # Pixabay URLs end in _large.mp4 etc — use last path component + prefix
        fname = "pixabay_" + url.split("?")[0].split("/")[-1]
        if not fname.endswith(".mp4"):
            fname += ".mp4"
        dest = dest_dir / fname
        if dest.exists():
            return dest

        logger.info("Downloading (pixabay) %s → %s", url[:80], dest.name)
        with self._session.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
        return dest


# ── Caption Renderer ───────────────────────────────────────────────────────────

class CaptionRenderer:
    """
    PIL-based TikTok/Shorts style caption renderer with language-aware fonts.

    Style:
      - Language-correct font (Impact for Latin, Devanagari for Hindi, etc.)
      - White text, thick black stroke
      - Semi-transparent rounded pill background
      - Bottom-anchored, 90px from frame edge
      - 120ms fade-in per caption
    """

    FONT_SIZE       = 62
    STROKE_W        = 4
    PADDING_X       = 32
    PADDING_Y       = 16
    CORNER_R        = 16
    BG_ALPHA        = 170
    TEXT_COLOR      = (255, 255, 255)
    HIGHLIGHT_COLOR = (255, 228, 0)    # yellow — first word "pop" highlight
    STROKE_COLOR    = (0, 0, 0)
    BG_COLOR        = (0, 0, 0, BG_ALPHA)
    BOTTOM_MARGIN   = 240    # YouTube Shorts UI (like/share/subscribe) covers bottom ~200px
    FADE_IN_SEC     = 0.12   # caption fade-in duration

    def __init__(self, lang: str = "en") -> None:
        self._font = self._load_font(lang)
        self._lang = lang

    # ── Public ─────────────────────────────────────────────────────────────────

    def make_clip(self, text: str, start: float, end: float) -> ImageClip:
        """
        Return a fading-in transparent ImageClip for the caption, with the first
        word highlighted. Used as a fallback when word-level timings are unavailable.
        """
        words    = text.split()
        frame    = self._render_frame(words, active_idx=0 if words else None)
        arr      = np.array(frame)
        duration = max(end - start, 0.1)

        clip = (
            ImageClip(arr)
            .with_duration(duration)
            .with_start(start)
            .with_position(self._position(frame.height))
            .with_effects([vfx.CrossFadeIn(min(self.FADE_IN_SEC, duration * 0.5))])
        )
        return clip

    def make_word_clips(self, caption_group: dict) -> list[ImageClip]:
        """
        Kinetic per-word captions: one ImageClip per word in the group, with the
        currently-spoken word highlighted in yellow. The active-word color swap
        as each word is spoken is the signature 2026 viral-Shorts caption style.

        Falls back to a single `make_clip` call when word data is missing.
        """
        words_meta = caption_group.get("words")
        if not words_meta:
            return [self.make_clip(caption_group["text"], caption_group["start"], caption_group["end"])]

        word_strings = [w["word"] for w in words_meta]
        group_end    = caption_group["end"]

        clips: list[ImageClip] = []
        for idx, w in enumerate(words_meta):
            frame = self._render_frame(word_strings, active_idx=idx)
            arr   = np.array(frame)

            # Active-word clip runs from this word's start to next word's start
            # (or group end for the final word) — prevents gaps between highlights.
            start    = w["start"]
            end      = words_meta[idx + 1]["start"] if idx + 1 < len(words_meta) else group_end
            duration = max(end - start, 0.05)

            clip = (
                ImageClip(arr)
                .with_duration(duration)
                .with_start(start)
                .with_position(self._position(frame.height))
            )
            # Fade only the FIRST word's entry — later words swap in-place.
            if idx == 0:
                clip = clip.with_effects([vfx.CrossFadeIn(min(self.FADE_IN_SEC, duration * 0.5))])
            clips.append(clip)

        return clips

    # ── Internal ───────────────────────────────────────────────────────────────

    def _render_frame(self, words: list[str], active_idx: int | None = 0) -> Image.Image:
        """
        Render the caption frame with `words` laid out, highlighting `active_idx`
        in yellow. All other words render in white. Pass active_idx=None for
        all-white (no highlight). Layout is identical across all active_idx
        values so successive frames swap in-place without jitter.
        """
        font  = self._font
        dummy = ImageDraw.Draw(Image.new("RGBA", (1, 1)))

        if not words:
            words = [""]

        # Wrap words into lines that fit the frame width (keeps index tracking)
        lines_of_idx = self._wrap_word_indices(dummy, words, font, max_width=W - 80)

        # Measure each line for layout
        line_texts   = [" ".join(words[i] for i in line) for line in lines_of_idx]
        line_bboxes  = [dummy.textbbox((0, 0), t, font=font, stroke_width=self.STROKE_W)
                        for t in line_texts]
        line_heights = [max(bb[3] - bb[1], 1) for bb in line_bboxes]
        line_widths  = [max(bb[2] - bb[0], 1) for bb in line_bboxes]
        line_gap     = 10

        block_w = max(line_widths) if line_widths else 1
        block_h = sum(line_heights) + line_gap * max(len(line_widths) - 1, 0)

        img_w = block_w + self.PADDING_X * 2
        img_h = block_h + self.PADDING_Y * 2

        img  = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        draw.rounded_rectangle(
            [0, 0, img_w - 1, img_h - 1],
            radius=self.CORNER_R,
            fill=self.BG_COLOR,
        )

        y = self.PADDING_Y
        for li, line_indices in enumerate(lines_of_idx):
            line_text = line_texts[li]
            lw        = line_widths[li]
            x_start   = (img_w - lw) // 2

            # 1. Stroke pass — entire line at once for consistent outline
            draw.text(
                (x_start, y), line_text, font=font,
                fill=self.STROKE_COLOR + (255,),
                stroke_width=self.STROKE_W,
                stroke_fill=self.STROKE_COLOR + (255,),
            )

            # 2. Per-word fill pass at correct x-positions
            x = x_start
            space_w = draw.textlength(" ", font=font)
            for pos, word_idx in enumerate(line_indices):
                word  = words[word_idx]
                color = self.HIGHLIGHT_COLOR if word_idx == active_idx else self.TEXT_COLOR
                draw.text((x, y), word, font=font, fill=color + (255,))
                x += draw.textlength(word, font=font)
                if pos < len(line_indices) - 1:
                    x += space_w

            y += line_heights[li] + line_gap

        return img

    def _position(self, frame_h: int):
        y = H - frame_h - self.BOTTOM_MARGIN
        return ("center", max(0, y))

    def _wrap_word_indices(
        self,
        draw,
        words: list[str],
        font,
        max_width: int,
    ) -> list[list[int]]:
        """Split word indices into lines that fit max_width. Preserves indices
        so the caller can map word position → color."""
        lines: list[list[int]] = []
        current: list[int] = []
        current_text = ""
        for idx, word in enumerate(words):
            test = (current_text + " " + word).strip()
            bb   = draw.textbbox((0, 0), test, font=font, stroke_width=self.STROKE_W)
            if bb[2] - bb[0] > max_width and current:
                lines.append(current)
                current = [idx]
                current_text = word
            else:
                current.append(idx)
                current_text = test
        if current:
            lines.append(current)
        return lines or [[0]]

    @classmethod
    def _load_font(cls, lang: str) -> ImageFont.FreeTypeFont:
        candidates = _FONT_MAP.get(lang, _FONT_MAP["default"]) + _FONT_MAP["default"]
        for path in candidates:
            try:
                return ImageFont.truetype(path, cls.FONT_SIZE)
            except Exception:
                pass
        logger.warning("No suitable font found for lang='%s' — using PIL default", lang)
        return ImageFont.load_default()


# ── Hook card (Pattern Interrupt opening frame) ────────────────────────────────

def _render_hook_card(
    hook_text: str,
    duration: float,
    accent_color: tuple[int, int, int] = (255, 228, 0),
) -> ImageClip:
    """
    Render a full-frame bold text card for the first ~0.8s of the video.

    Goal: the very first frame must NOT look like generic stock footage —
    that is the #1 swipe-away signal on Shorts. A hard-cut bold typography
    intro acts as a visual pattern interrupt that holds attention long enough
    for the hook line to land.

    Layout: black background, accent diagonal stripe behind text, huge stacked
    Impact-style words centered. A subtle 1.0 → 1.04 scale punch is applied
    via Ken-Burns-style transform when the clip is composited.
    """
    img = Image.new("RGBA", (W, H), (10, 10, 10, 255))
    draw = ImageDraw.Draw(img)

    # Diagonal accent stripe
    stripe = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    stripe_draw = ImageDraw.Draw(stripe)
    stripe_draw.polygon(
        [(0, int(H * 0.45)), (W, int(H * 0.30)), (W, int(H * 0.55)), (0, int(H * 0.70))],
        fill=accent_color + (60,),
    )
    img = Image.alpha_composite(img, stripe)
    draw = ImageDraw.Draw(img)

    # Pick the boldest available font
    font = None
    for path in _FONT_MAP["default"]:
        try:
            font = ImageFont.truetype(path, 140)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()

    # Word-wrap the hook at ~10 chars per line for big readable type
    words = hook_text.upper().strip().split()
    lines: list[str] = []
    cur = ""
    for w in words:
        test = (cur + " " + w).strip()
        bb = draw.textbbox((0, 0), test, font=font, stroke_width=6)
        if bb[2] - bb[0] > W - 120 and cur:
            lines.append(cur)
            cur = w
        else:
            cur = test
    if cur:
        lines.append(cur)

    line_h = 160
    total_h = line_h * len(lines)
    y = (H - total_h) // 2
    for line in lines:
        line_w = draw.textlength(line, font=font)
        x = (W - line_w) // 2
        # Drop shadow
        draw.text((x + 6, y + 8), line, font=font, fill=(0, 0, 0, 220))
        draw.text(
            (x, y), line, font=font,
            fill=(255, 255, 255, 255),
            stroke_width=6,
            stroke_fill=(0, 0, 0, 255),
        )
        y += line_h

    arr = np.array(img)
    clip = ImageClip(arr).with_duration(duration)

    # Subtle zoom-punch (1.00 → 1.04) over the card duration for energy
    def zoom_punch(get_frame, t):
        frame = get_frame(t)
        progress = min(t / max(duration, 0.001), 1.0)
        z = 1.0 + 0.04 * progress
        h, w = frame.shape[:2]
        nh, nw = int(h / z), int(w / z)
        y0, x0 = (h - nh) // 2, (w - nw) // 2
        cropped = frame[y0:y0 + nh, x0:x0 + nw]
        return np.array(
            Image.fromarray(cropped.astype(np.uint8)).resize((w, h), Image.LANCZOS)
        )

    return clip.transform(zoom_punch)


# ── Cinematic frame processor ──────────────────────────────────────────────────

def _cinematic_frame(frame: np.ndarray) -> np.ndarray:
    """
    Apply warm color grade + subtle film grain in one pass per frame.

    Color grade (breaks uniform stock-footage look):
      - Red   +4%  +3 lift  → warm, golden tones
      - Green +1%            → natural midtones
      - Blue  -9%            → removes the cold/clean digital look

    Film grain (randomised per-frame — cannot be fingerprinted):
      - Gaussian noise σ=6, mean=0
      - Physically impossible to match frame-for-frame

    Both together make the video feel hand-graded and unique.
    """
    img = frame.astype(np.float32)

    # Warm color grade
    img[:, :, 0] = np.clip(img[:, :, 0] * 1.04 + 3,  0, 255)   # R
    img[:, :, 1] = np.clip(img[:, :, 1] * 1.01,       0, 255)   # G
    img[:, :, 2] = np.clip(img[:, :, 2] * 0.91,       0, 255)   # B

    # Film grain
    grain = np.random.normal(0, 6, frame.shape).astype(np.float32)
    img   = np.clip(img + grain, 0, 255)

    return img.astype(np.uint8)


# ── Ken Burns effect ───────────────────────────────────────────────────────────

def _apply_ken_burns(clip: VideoFileClip, zoom_ratio: float = 0.06) -> VideoFileClip:
    """
    Subtle slow-zoom-in animation (Ken Burns effect).

    Each clip zooms from 1.0× → (1 + zoom_ratio)× over its duration.
    Implemented via per-frame numpy crop + PIL resize for speed.
    """
    z_end = 1.0 + abs(zoom_ratio)

    def zoom_and_grade(get_frame, t):
        frame    = get_frame(t)
        progress = t / max(clip.duration, 0.001)
        z        = 1.0 + (z_end - 1.0) * progress
        h, w     = frame.shape[:2]
        nh, nw   = int(h / z), int(w / z)
        y0, x0   = (h - nh) // 2, (w - nw) // 2
        cropped  = frame[y0:y0 + nh, x0:x0 + nw]
        resized  = np.array(
            Image.fromarray(cropped.astype(np.uint8)).resize((w, h), Image.LANCZOS)
        )
        # Color grade + grain applied in the same pass — no extra iteration
        return _cinematic_frame(resized)

    return clip.transform(zoom_and_grade)


# ── Video Editor ───────────────────────────────────────────────────────────────

class VideoEditorService:
    """Assembles the final Short from audio + stock footage + captions."""

    def __init__(
        self,
        pexels_api_key: str | None = None,
        pixabay_api_key: str | None = None,
        output_dir: str | None = None,
        clip_cache_dir: str | None = None,
    ) -> None:
        self._fetcher         = PexelsFetcher(api_key=pexels_api_key)
        self._pixabay_fetcher = PixabayFetcher(api_key=pixabay_api_key)
        self.output_dir = Path(output_dir or os.getenv("OUTPUT_VIDEO_DIR", "output/video"))
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.clip_cache_dir = Path(clip_cache_dir or "output/video/cache")
        self.clip_cache_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "VideoEditorService ready — output=%s  pixabay=%s",
            self.output_dir, "on" if self._pixabay_fetcher.enabled else "off",
        )

    # ── Public API ─────────────────────────────────────────────────────────────

    def render(
        self,
        audio_path: str | Path,
        visual_keywords: list[str],
        captions: list[dict],
        output_filename: str,
        lang: str = "en",
        hook_text: str | None = None,
        hook_card_duration: float = 0.5,
    ) -> Path:
        """
        Full render pipeline.

        Parameters
        ----------
        audio_path       : Path to voiceover MP3.
        visual_keywords  : Pexels search terms.
        captions         : [{text, start, end}, ...].
        output_filename  : e.g. "abc123_final.mp4".
        lang             : ISO 639-1 language code for caption font selection.
        """
        audio_clip     = AudioFileClip(str(audio_path))
        total_duration = audio_clip.duration
        logger.info("Audio duration: %.2fs — compositing video…", total_duration)
        logger.info("[PROGRESS:editing:5]")

        # Audio loop fade — small fade on the trailing edge so the loop join
        # CTA → hook is seamless instead of a hard click. The first frames stay
        # full-volume so the hook word still hits with full energy.
        try:
            audio_clip = audio_clip.with_effects([afx.AudioFadeOut(0.18)])
        except Exception as exc:
            logger.debug("Audio fade-out skipped: %s", exc)

        # 1. HD background with Ken Burns animation  (progress 8 → 65 inside)
        bg = self._build_background(visual_keywords, total_duration)

        # 2. Attach audio
        bg_with_audio = bg.with_audio(audio_clip)
        logger.info("[PROGRESS:editing:68]")

        # 3. Language-aware captions with fade-in
        caption_renderer = CaptionRenderer(lang=lang)
        caption_clips    = self._build_caption_clips(captions, caption_renderer)
        logger.info("[PROGRESS:editing:75]")

        layers: list = [bg_with_audio, *caption_clips]

        # 4. Hook text card (pattern-interrupt overlay for the first ~0.8s)
        if hook_text:
            try:
                card_dur = min(hook_card_duration, total_duration * 0.3, 1.2)
                hook_card = _render_hook_card(hook_text, duration=card_dur).with_start(0)
                layers.insert(1, hook_card)  # Above background, below captions
                logger.info("Hook card overlay added (duration=%.2fs)", card_dur)
            except Exception as exc:
                logger.warning("Hook card render failed — continuing without: %s", exc)

        final = (
            CompositeVideoClip(layers, size=(W, H))
            if len(layers) > 1 else bg_with_audio
        )

        # 4. Write to disk
        if not output_filename.endswith(".mp4"):
            output_filename += ".mp4"
        output_path = self.output_dir / output_filename

        logger.info("Rendering → %s  (this may take a few minutes…)", output_path)
        logger.info("[PROGRESS:editing:80]")
        final.write_videofile(
            str(output_path),
            fps=FPS,
            codec="libx264",
            audio_codec="aac",
            temp_audiofile=str(self.output_dir / "tmp_audio.m4a"),
            remove_temp=True,
            logger=None,
        )
        logger.info("Render complete: %s", output_path)
        # note: orchestrator logs [PROGRESS:editing:100] after this returns
        return output_path

    # ── Background builder ─────────────────────────────────────────────────────

    def _build_background(self, visual_keywords: list[str], total_duration: float):
        clip_paths: list[Path] = []
        n_kw = max(len(visual_keywords), 1)

        for kw_idx, kw in enumerate(visual_keywords):
            # Spread fetch progress: 8 % → 55 %  across all keywords
            fetch_pct = 8 + int(47 * kw_idx / n_kw)
            logger.info("[PROGRESS:editing:%d]", fetch_pct)

            per_keyword: list[Path] = []

            # Tier 1: Pexels — higher-curated quality, try first
            try:
                for url in self._fetcher.search(kw, per_page=6)[:3]:
                    per_keyword.append(self._fetcher.download(url, self.clip_cache_dir))
            except Exception as exc:
                logger.warning("Pexels fetch failed for '%s': %s", kw, exc)

            # Tier 2: Pixabay — fills gaps Pexels can't cover, adds variety
            if self._pixabay_fetcher.enabled and len(per_keyword) < 3:
                try:
                    need = 3 - len(per_keyword)
                    for url in self._pixabay_fetcher.search(kw, per_page=5)[:need]:
                        per_keyword.append(self._pixabay_fetcher.download(url, self.clip_cache_dir))
                except Exception as exc:
                    logger.warning("Pixabay fetch failed for '%s': %s", kw, exc)

            clip_paths.extend(per_keyword)

        if not clip_paths:
            logger.error("No clips fetched — using black fallback")
            return ColorClip(size=(W, H), color=(0, 0, 0), duration=total_duration)

        # Cadence: Shorts retention drops sharply when a single clip lingers >2.5s.
        # Force enough slots so average slot_duration ≤ MAX_SLOT_SEC. If we don't
        # have enough unique clips, cycle the pool with a shuffle to avoid
        # back-to-back duplicates.
        MAX_SLOT_SEC = 2.2
        MIN_SLOTS = math.ceil(total_duration / MAX_SLOT_SEC)
        n_slots = max(len(clip_paths), MIN_SLOTS)

        if n_slots > len(clip_paths):
            pool = list(clip_paths)
            random.shuffle(pool)
            slots: list[Path] = []
            while len(slots) < n_slots:
                # Reshuffle each cycle and avoid placing the same clip back-to-back
                random.shuffle(pool)
                for p in pool:
                    if slots and slots[-1] == p:
                        continue
                    slots.append(p)
                    if len(slots) >= n_slots:
                        break
        else:
            slots = list(clip_paths)

        slot_duration = total_duration / len(slots)
        logger.info(
            "Clip cadence: %d slots × %.2fs (pool=%d, target≤%.1fs/slot)",
            len(slots), slot_duration, len(clip_paths), MAX_SLOT_SEC,
        )
        processed: list[VideoFileClip] = []
        n_slots_total = max(len(slots), 1)

        for i, path in enumerate(slots):
            # Spread clip-processing progress: 57 % → 65 %
            proc_pct = 57 + int(8 * i / n_slots_total)
            logger.info("[PROGRESS:editing:%d]", proc_pct)
            try:
                raw      = VideoFileClip(str(path))
                trim_end = min(slot_duration, raw.duration)

                # Alternate zoom direction: even clips zoom in, odd clips zoom out
                zoom_ratio = 0.06 if i % 2 == 0 else -0.04

                vc = (
                    raw
                    .subclipped(0, trim_end)
                    .resized((W, H))
                    .with_duration(slot_duration)
                )
                vc = _apply_ken_burns(vc, zoom_ratio=abs(zoom_ratio))
                processed.append(vc)
            except Exception as exc:
                logger.warning("Clip failed %s: %s", path.name, exc)

        if not processed:
            return ColorClip(size=(W, H), color=(0, 0, 0), duration=total_duration)

        combined = concatenate_videoclips(processed, method="compose")
        if combined.duration < total_duration:
            loops    = int(total_duration / combined.duration) + 1
            combined = concatenate_videoclips([combined] * loops, method="compose")

        return combined.subclipped(0, total_duration)

    # ── Caption builder ────────────────────────────────────────────────────────

    def _build_caption_clips(
        self,
        captions: list[dict],
        renderer: CaptionRenderer,
    ) -> list[ImageClip]:
        clips: list[ImageClip] = []
        kinetic_groups = 0
        for cap in captions:
            try:
                if cap.get("words"):
                    clips.extend(renderer.make_word_clips(cap))
                    kinetic_groups += 1
                else:
                    clips.append(renderer.make_clip(cap["text"], cap["start"], cap["end"]))
            except Exception as exc:
                logger.warning("Caption skipped '%s': %s", cap["text"][:30], exc)
        logger.info(
            "Built %d caption overlay clips (kinetic groups=%d, lang=%s)",
            len(clips), kinetic_groups, renderer._lang,
        )
        return clips
