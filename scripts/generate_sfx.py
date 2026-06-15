"""
Generates synthetic SFX assets for the video pipeline.

Creates:
  assets/sfx/whoosh.mp3  — scene-transition whoosh (~600ms)
  assets/sfx/impact.mp3  — hook-landing bass punch (~500ms)

No downloads or API keys needed — built entirely from numpy + pydub.

Run:
    python scripts/generate_sfx.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from pydub import AudioSegment

SAMPLE_RATE = 44100
SFX_DIR     = Path(__file__).resolve().parent.parent / "assets" / "sfx"


# ── Signal builders ────────────────────────────────────────────────────────────

def _make_whoosh(duration_ms: int = 600) -> np.ndarray:
    """
    Descending bandpass noise sweep — the classic camera-pan / scene-cut whoosh.
    Shape: soft attack → sustained hiss → fast tail-off.
    """
    n = int(SAMPLE_RATE * duration_ms / 1000)
    t = np.linspace(0, duration_ms / 1000, n, dtype=np.float32)

    # White noise source
    rng   = np.random.default_rng(42)
    noise = rng.standard_normal(n).astype(np.float32)

    # High-pass IIR (~1 kHz cutoff) — removes muddy lows
    alpha = 0.92
    hp = np.empty(n, dtype=np.float32)
    hp[0] = noise[0]
    for i in range(1, n):
        hp[i] = alpha * (hp[i - 1] + noise[i] - noise[i - 1])

    # Descending pitch sweep layered on top (3kHz → 600Hz)
    f_start, f_end = 3000.0, 600.0
    freqs  = f_start + (f_end - f_start) * (t / t[-1])
    phase  = np.cumsum(2.0 * np.pi * freqs / SAMPLE_RATE)
    sweep  = np.sin(phase).astype(np.float32) * 0.25

    # Amplitude envelope: quick attack (first 8%) → hold → exponential tail
    attack_end = int(n * 0.08)
    env        = np.ones(n, dtype=np.float32)
    env[:attack_end] = np.linspace(0.0, 1.0, attack_end)
    decay_start = int(n * 0.45)
    decay_len   = n - decay_start
    env[decay_start:] = np.exp(-np.linspace(0, 5, decay_len))

    signal = (hp * 0.6 + sweep) * env
    # Normalise to 70% headroom
    peak = np.max(np.abs(signal)) + 1e-9
    return (signal / peak * 0.70).astype(np.float32)


def _make_impact(duration_ms: int = 500) -> np.ndarray:
    """
    808-style sub-bass thump: transient click → pitched boom → tail.
    Sits in the 50–250 Hz range — complements the TTS voice without masking it.
    """
    n = int(SAMPLE_RATE * duration_ms / 1000)
    t = np.linspace(0, duration_ms / 1000, n, dtype=np.float32)

    # Sub-bass layer (55 Hz) — the 'boom'
    sub_env = np.exp(-t * 10.0)
    sub     = np.sin(2.0 * np.pi * 55.0 * t) * sub_env

    # Mid punch (180 Hz) — body of the hit
    mid_env = np.exp(-t * 15.0)
    mid     = np.sin(2.0 * np.pi * 180.0 * t) * mid_env * 0.55

    # High transient click (first 4 ms) — the attack crack
    click_n  = int(SAMPLE_RATE * 0.004)
    rng      = np.random.default_rng(7)
    click    = np.zeros(n, dtype=np.float32)
    raw_click = rng.standard_normal(click_n).astype(np.float32)
    # Taper click so it doesn't pop
    raw_click *= np.linspace(1.0, 0.0, click_n)
    click[:click_n] = raw_click * 0.45

    # Second harmonic shimmer (110 Hz) — adds weight
    shimmer_env = np.exp(-t * 20.0)
    shimmer     = np.sin(2.0 * np.pi * 110.0 * t) * shimmer_env * 0.30

    signal = sub + mid + shimmer + click
    peak   = np.max(np.abs(signal)) + 1e-9
    return (signal / peak * 0.85).astype(np.float32)


# ── Audio I/O ──────────────────────────────────────────────────────────────────

def _to_audiosegment(signal: np.ndarray, sample_rate: int = SAMPLE_RATE) -> AudioSegment:
    """Convert float32 [-1, 1] numpy array → pydub AudioSegment (mono)."""
    pcm = (signal * 32767).astype(np.int16)
    return AudioSegment(
        pcm.tobytes(),
        frame_rate=sample_rate,
        sample_width=2,   # int16 = 2 bytes
        channels=1,
    )


def _save(seg: AudioSegment, path: Path, fade_out_ms: int = 80) -> None:
    seg = seg.fade_in(10).fade_out(fade_out_ms)
    path.parent.mkdir(parents=True, exist_ok=True)
    seg.export(str(path), format="mp3", bitrate="192k")
    size_kb = path.stat().st_size / 1024
    print(f"  ✓  {path.relative_to(path.parent.parent.parent)}  "
          f"({len(seg)}ms, {size_kb:.0f} KB)")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    print("Generating synthetic SFX assets…\n")

    whoosh_path = SFX_DIR / "whoosh.mp3"
    impact_path = SFX_DIR / "impact.mp3"

    try:
        _save(_to_audiosegment(_make_whoosh()), whoosh_path)
        _save(_to_audiosegment(_make_impact()), impact_path)
    except Exception as exc:
        print(f"\n✗  Failed: {exc}")
        print("   Make sure ffmpeg is installed (needed by pydub for MP3 export).")
        return 1

    print("\nDone — SFX generated into assets/sfx/.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
