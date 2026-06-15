"""
Setup script for audio assets (SFX).

Creates the required directory structure and reports what's present.
Prints curated download links to free/royalty-free sources.

Note: Shorts are produced with a clean voiceover and no background music
by design — there is no music bed to set up.

Run:
    python scripts/setup_assets.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SFX_DIR   = ROOT / "assets" / "sfx"

REQUIRED_SFX = {
    "whoosh.mp3": "Plays at every scene transition (subtle). Keep 0.3–0.8s long.",
    "impact.mp3": "Plays at ~2s mark where the hook lands. Bass-heavy punch, <1s.",
}

SFX_SOURCES = [
    ("Mixkit (direct download, no login)", "https://mixkit.co/free-sound-effects/whoosh/"),
    ("Mixkit impact hits",                 "https://mixkit.co/free-sound-effects/impact/"),
    ("Pixabay SFX",                        "https://pixabay.com/sound-effects/search/whoosh/"),
    ("Freesound.org",                      "https://freesound.org/search/?q=whoosh"),
]


def _report_dir(label: str, dir_path: Path, exts: tuple[str, ...]) -> int:
    files = [p for ext in exts for p in dir_path.glob(f"*{ext}")]
    tick  = "✓" if files else "·"
    print(f"  [{tick}] {label}: {dir_path.relative_to(ROOT)}/  ({len(files)} file(s))")
    for p in sorted(files):
        size_kb = p.stat().st_size / 1024
        print(f"        - {p.name} ({size_kb:.0f} KB)")
    return len(files)


def main() -> int:
    SFX_DIR.mkdir(parents=True, exist_ok=True)

    print("── Audio asset status ────────────────────────────────────────────────")
    n_sfx   = _report_dir("sfx",   SFX_DIR,   (".mp3", ".wav"))

    print()
    print("── Required SFX filenames ────────────────────────────────────────────")
    for fname, purpose in REQUIRED_SFX.items():
        present = (SFX_DIR / fname).exists()
        mark = "✓" if present else "·"
        print(f"  [{mark}] assets/sfx/{fname}")
        print(f"        {purpose}")

    print()
    print("── Where to get audio (free / royalty-free) ──────────────────────────")
    print(f"  SFX — save as assets/sfx/whoosh.mp3 and assets/sfx/impact.mp3:")
    for name, url in SFX_SOURCES:
        print(f"    • {name}: {url}")

    print()
    if n_sfx == 0:
        print("⚠  Pipeline works without these — SFX are skipped gracefully.")
        return 1

    print("✓  All audio assets present.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
