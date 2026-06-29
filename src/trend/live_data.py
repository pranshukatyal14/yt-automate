"""
Live football data — real World Cup results & scorers from football-data.org.

Used to GROUND the trend researcher in real events instead of letting the AI
fabricate match results (which flop and risk the channel). Every function is
hard-wrapped: any failure (no key, network, API change) returns empty so the
pipeline silently falls back to the existing AI trend research — it can never
break a run.

Free tier: ~10 req/min, top competitions free. Auth via X-Auth-Token header.
"""
from __future__ import annotations

import logging
import os
import subprocess
import json as _json
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_BASE = "https://api.football-data.org/v4"
_COMP = os.getenv("FOOTBALL_COMPETITION", "WC")  # WC = FIFA World Cup


def _get(path: str) -> dict:
    """GET via curl subprocess (consistent with the Groq path; avoids hanging libs)."""
    key = os.getenv("FOOTBALL_API_KEY", "")
    if not key:
        return {}
    try:
        res = subprocess.run(
            ["curl", "-s", "--max-time", "15",
             "-H", f"X-Auth-Token: {key}", f"{_BASE}{path}"],
            capture_output=True, text=True, timeout=20,
        )
        if res.returncode == 0 and res.stdout:
            return _json.loads(res.stdout)
    except Exception as exc:
        logger.warning("live_data fetch failed (%s) — falling back to AI trends", str(exc)[:100])
    return {}


def fetch_recent_matches(days: int = 3) -> list[dict]:
    """Recent FINISHED matches: [{date, home, away, hs, as, result}]."""
    data = _get(f"/competitions/{_COMP}/matches?status=FINISHED")
    out: list[dict] = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date()
    for m in data.get("matches", []):
        try:
            d = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00")).date()
            if d < cutoff:
                continue
            ft = m["score"]["fullTime"]
            hs, as_ = ft.get("home"), ft.get("away")
            if hs is None:
                continue
            out.append({
                "date": d.isoformat(),
                "home": m["homeTeam"]["name"], "away": m["awayTeam"]["name"],
                "hs": hs, "as": as_,
                "result": f"{m['homeTeam']['name']} {hs}-{as_} {m['awayTeam']['name']}",
            })
        except Exception:
            continue
    return out


def fetch_top_scorers(limit: int = 8) -> list[dict]:
    """Top scorers: [{player, team, goals}]."""
    data = _get(f"/competitions/{_COMP}/scorers?limit={limit}")
    out: list[dict] = []
    for s in data.get("scorers", []):
        try:
            out.append({
                "player": s["player"]["name"],
                "team": s["team"]["name"],
                "goals": s.get("goals", 0),
            })
        except Exception:
            continue
    return out


def build_context_block(days: int = 3) -> str:
    """
    Build a GROUND-TRUTH block of real recent results + scorers for the trend
    prompt. Returns "" if no data (→ pipeline falls back to AI trends).
    """
    try:
        matches = fetch_recent_matches(days=days)
        scorers = fetch_top_scorers()
        if not matches and not scorers:
            return ""
        lines = ["REAL WORLD CUP DATA (last %d days) — base topics on THESE actual facts, do NOT invent results:" % days]
        if matches:
            lines.append("Recent results:")
            for m in matches[-12:]:
                lines.append(f"  - {m['date']}: {m['result']}")
        if scorers:
            lines.append("Top scorers so far:")
            for s in scorers:
                lines.append(f"  - {s['player']} ({s['team']}): {s['goals']} goals")
        logger.info("live_data: grounded with %d real matches + %d scorers", len(matches), len(scorers))
        return "\n".join(lines)
    except Exception as exc:
        logger.warning("live_data.build_context_block failed (%s)", str(exc)[:100])
        return ""
