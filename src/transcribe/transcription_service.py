"""
Transcription Service — faster-whisper (local, no API key).

Ingests an audio file and returns word-level timestamps:
[
    {"word": "hello", "start": 0.00, "end": 0.42},
    {"word": "world", "start": 0.45, "end": 0.80},
    ...
]

Model sizes vs accuracy/speed trade-off (CPU):
  tiny   → fastest,  lowest accuracy  (~32x realtime)
  base   → fast,     good accuracy    (~16x realtime)  ← default
  small  → moderate, better accuracy  (~6x  realtime)
  medium → slow,     high accuracy    (~2x  realtime)
  large  → slowest,  best accuracy    (~1x  realtime)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import NamedTuple

from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)


# ── Data structure ─────────────────────────────────────────────────────────────

class WordTimestamp(NamedTuple):
    word:  str
    start: float   # seconds
    end:   float   # seconds

    def to_dict(self) -> dict:
        return {"word": self.word, "start": round(self.start, 3), "end": round(self.end, 3)}


# ── Service ────────────────────────────────────────────────────────────────────

class TranscriptionService:
    """Wraps faster-whisper for word-level timestamp extraction."""

    def __init__(
        self,
        model_size: str | None = None,
        device: str | None = None,
        compute_type: str | None = None,
    ) -> None:
        self.model_size   = model_size   or os.getenv("WHISPER_MODEL_SIZE", "base")
        self.device       = device       or os.getenv("WHISPER_DEVICE", "cpu")
        # float16 only works on CUDA; cpu must use int8 or float32
        self.compute_type = compute_type or ("float16" if self.device == "cuda" else "int8")

        logger.info(
            "Loading Whisper model '%s' on %s (%s) — first run will download weights…",
            self.model_size, self.device, self.compute_type,
        )
        self._model = WhisperModel(
            self.model_size,
            device=self.device,
            compute_type=self.compute_type,
        )
        logger.info("Whisper model loaded.")

    # ── Public API ─────────────────────────────────────────────────────────────

    def transcribe(self, audio_path: str | Path, language: str | None = None) -> list[dict]:
        """
        Transcribe *audio_path* and return word-level timestamps.

        Parameters
        ----------
        audio_path : Path to an MP3, WAV, or any ffmpeg-readable file.
        language   : ISO 639-1 code (e.g. "en", "hi", "es"). None = auto-detect.

        Returns
        -------
        list[dict] — each dict has keys: word, start, end
        """
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        lang = language if language and language != "en" else "en"
        logger.info("Transcribing [lang=%s]: %s", lang, audio_path)

        segments, info = self._model.transcribe(
            str(audio_path),
            word_timestamps=True,
            language=lang,
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300},
        )

        logger.info(
            "Detected language '%s' (prob=%.2f), duration=%.1fs",
            info.language, info.language_probability, info.duration,
        )

        words: list[dict] = []
        for segment in segments:
            if segment.words is None:
                logger.warning("Segment has no word-level data — check model supports word_timestamps")
                continue
            for w in segment.words:
                cleaned = w.word.strip()
                if cleaned:
                    words.append(
                        WordTimestamp(word=cleaned, start=w.start, end=w.end).to_dict()
                    )

        logger.info("Transcribed %d words from '%s'", len(words), audio_path.name)
        return words

    # ── Utility ────────────────────────────────────────────────────────────────

    @staticmethod
    def group_into_captions(
        words: list[dict],
        words_per_caption: int = 3,
    ) -> list[dict]:
        """
        Group word timestamps into caption chunks for overlay rendering.

        Returns
        -------
        list[dict] — [{text, start, end, words: [{word, start, end}, ...]}, ...]
        The per-word list enables kinetic active-word highlighting.
        """
        captions = []
        for i in range(0, len(words), words_per_caption):
            chunk = words[i : i + words_per_caption]
            captions.append({
                "text":  " ".join(w["word"] for w in chunk),
                "start": chunk[0]["start"],
                "end":   chunk[-1]["end"],
                "words": [
                    {"word": w["word"], "start": w["start"], "end": w["end"]}
                    for w in chunk
                ],
            })
        return captions
