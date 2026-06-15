"""
Voice Service — Microsoft Edge TTS + audio post-processing.

Steps:
  1. Synthesise speech via edge-tts (Microsoft neural voices, no API key).
  2. Normalize loudness + soft dynamic compression via pydub.
  3. Apply subtle fade-in / fade-out.
  4. LUFS-normalize to YouTube's -14 LUFS playback target.

The voiceover is delivered clean (no background music) by design.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import edge_tts
from pydub import AudioSegment
from pydub.effects import compress_dynamic_range, normalize

logger = logging.getLogger(__name__)

# ── Language → voice map ───────────────────────────────────────────────────────

VOICE_MAP: dict[str, str] = {
    "en": "en-US-BrianNeural",
    "hi": "hi-IN-MadhurNeural",
    "es": "es-MX-JorgeNeural",
    "fr": "fr-FR-HenriNeural",
    "de": "de-DE-ConradNeural",
    "pt": "pt-BR-AntonioNeural",
    "ja": "ja-JP-KeitaNeural",
    "ko": "ko-KR-InJoonNeural",
    "zh": "zh-CN-YunxiNeural",
    "ar": "ar-SA-HamedNeural",
}

DEFAULT_RATE   = "+25%"   # High-energy narrator pace — denser script, more retention
DEFAULT_PITCH  = "+0Hz"

# YouTube's loudness target. Most Shorts sit in the -16 to -13 LUFS band; -14 LUFS
# matches the YouTube playback normalizer so our videos don't get attenuated.
TARGET_LUFS    = -14.0
TARGET_TP      = -1.5   # true-peak ceiling (dBTP) — avoids clipping
TARGET_LRA     = 11.0   # loudness range


# ── Service ────────────────────────────────────────────────────────────────────

class VoiceService:
    """Converts script text → processed MP3 via Microsoft Edge TTS."""

    def __init__(
        self,
        lang: str = "en",
        voice: str | None = None,
        rate: str | None = None,
        pitch: str | None = None,
        output_dir: str | None = None,
    ) -> None:
        if voice:
            self.voice = voice
        else:
            _lang = lang or os.getenv("TTS_LANG", "en")
            self.voice = os.getenv("TTS_VOICE") or VOICE_MAP.get(_lang, VOICE_MAP["en"])

        self.rate  = rate  or os.getenv("TTS_RATE", DEFAULT_RATE)
        self.pitch = pitch or DEFAULT_PITCH
        self.output_dir = Path(output_dir or os.getenv("OUTPUT_AUDIO_DIR", "output/audio"))
        self.output_dir.mkdir(parents=True, exist_ok=True)
        logger.info("VoiceService — voice=%s rate=%s", self.voice, self.rate)

    # ── Public API ─────────────────────────────────────────────────────────────

    def synthesise(self, text: str, filename: str) -> Path:
        """
        Synthesise *text* → processed MP3 at output_dir/<filename>.

        Pipeline: TTS → normalize → compress → fade → LUFS-normalize → save
        """
        if not filename.endswith(".mp3"):
            filename += ".mp3"

        raw_path  = self.output_dir / f"_raw_{filename}"
        final_path = self.output_dir / filename

        # 1. Raw TTS
        logger.info("Synthesising TTS → %s", raw_path.name)
        asyncio.run(self._async_tts(text, raw_path))

        # 2. Post-process
        logger.info("Post-processing audio…")
        audio = self._post_process(raw_path)

        # 3. Export
        audio.export(str(final_path), format="mp3", bitrate="192k")
        raw_path.unlink(missing_ok=True)

        size_kb = final_path.stat().st_size / 1024
        logger.info("Audio ready: %s (%.1f KB, %.1fs)", final_path.name, size_kb, len(audio) / 1000)
        return final_path

    # ── Post-processing ────────────────────────────────────────────────────────

    def _post_process(self, raw_path: Path) -> AudioSegment:
        voice = AudioSegment.from_mp3(str(raw_path))

        # Normalize loudness to -1 dBFS
        voice = normalize(voice)

        # Soft dynamic compression — evens out loud/quiet passages
        voice = compress_dynamic_range(
            voice,
            threshold=-20.0,
            ratio=2.5,
            attack=5.0,
            release=50.0,
        )

        # Short fade-in / fade-out on the whole track
        voice = voice.fade_in(300).fade_out(600)

        # Final stage: LUFS normalization to YouTube's target so our Short
        # plays at the same perceived volume as the previous video in the feed.
        # Quieter audio = instant swipe-away.
        voice = self._loudness_normalize(voice)
        return voice

    @staticmethod
    def _loudness_normalize(audio: AudioSegment) -> AudioSegment:
        """Run ffmpeg's loudnorm filter to hit -14 LUFS integrated."""
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.warning("ffmpeg not on PATH — skipping LUFS normalization")
            return audio

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "in.wav"
            dst = Path(tmp) / "out.wav"
            audio.export(str(src), format="wav")

            cmd = [
                ffmpeg, "-y", "-i", str(src),
                "-af",
                f"loudnorm=I={TARGET_LUFS}:TP={TARGET_TP}:LRA={TARGET_LRA}",
                "-ar", str(audio.frame_rate),
                str(dst),
            ]
            try:
                subprocess.run(cmd, check=True, capture_output=True, timeout=60)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                logger.warning("loudnorm failed — using pre-normalized audio: %s", exc)
                return audio

            normalized = AudioSegment.from_wav(str(dst))
            logger.info("Loudness normalized → %.1f LUFS target", TARGET_LUFS)
            return normalized

    # ── Internal ───────────────────────────────────────────────────────────────

    async def _async_tts(self, text: str, output_path: Path) -> None:
        communicate = edge_tts.Communicate(
            text=text,
            voice=self.voice,
            rate=self.rate,
            pitch=self.pitch,
        )
        await communicate.save(str(output_path))

    # ── Utility ────────────────────────────────────────────────────────────────

    @staticmethod
    def script_to_spoken_text(script: dict) -> str:
        parts = [
            script.get("hook", ""),
            script.get("body", ""),
            script.get("call_to_action", ""),
            script.get("comment_bait", ""),
        ]
        spoken_parts = []
        for part in parts:
            part = part.strip()
            if part and part[-1] not in ".!?।":
                part += "."
            if part:
                spoken_parts.append(part)
        return "  ".join(spoken_parts)
