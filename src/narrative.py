"""Narrative detection.

Three complementary layers:
  1. Dexscreener's ranked metas (market signal, no interpretation)
  2. Keyword clustering over the runners that survived filtering (local signal)
  3. A diff against previous days' snapshots (what is emerging)

Layer three is where the value is for a KOL: everyone sees what already ran,
almost nobody sees what is starting to form.
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sources import ds_trending_metas

log = logging.getLogger("narrative")

STOPWORDS = {
    "the", "and", "for", "coin", "token", "solana", "sol", "meme", "official",
    "inu", "new", "your", "you", "with", "that", "this", "from",
    "wif", "baby", "mini", "super",
}

THEMES = {
    "ai-agents":     ["ai", "agent", "gpt", "llm", "neural", "bot", "model", "eliza"],
    "animals":       ["dog", "cat", "frog", "bear", "bull", "monkey", "ape", "hippo",
                      "capybara", "penguin", "duck", "goat", "wolf", "fox"],
    "politics":      ["trump", "election", "president", "maga", "gov", "senate",
                      "policy", "vote"],
    "internet-culture": ["meme", "viral", "tiktok", "based", "chad", "wojak", "pepe",
                         "doge", "gm", "ngmi"],
    "defi-infra":    ["stake", "yield", "vault", "swap", "dex", "lend", "perp",
                      "liquid", "restake"],
    "gaming":        ["game", "play", "quest", "guild", "arena", "battle", "pixel"],
    "desci-rwa":     ["desci", "rwa", "science", "research", "bio", "health",
                      "energy", "carbon"],
    "celebrity":     ["elon", "musk", "kanye", "celeb", "star", "influencer"],
    "tradfi":        ["gold", "dollar", "fed", "rate", "bond", "etf", "index"],
}


def _tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Z]{3,}", (text or "").lower())
    return [w for w in words if w not in STOPWORDS]


def classify_theme(token) -> str:
    """Assign a theme from the token's name, symbol and description."""
    blob = " ".join([token.name, token.symbol, token.description]).lower()
    best, best_hits = "unclassified", 0
    for theme, keys in THEMES.items():
        hits = sum(1 for k in keys if k in blob)
        if hits > best_hits:
            best, best_hits = theme, hits
    return best


def cluster_runners(tokens: list) -> dict:
    """Group surviving runners by theme and by shared keyword."""
    by_theme: dict[str, list] = defaultdict(list)
    for t in tokens:
        by_theme[classify_theme(t)].append(t)

    # "unclassified" is only hidden when other themes remain to show: a day
    # that is entirely unclassified should say so rather than render empty.
    classified = [t for t in by_theme if t != "unclassified"]
    themes = []
    for theme, group in by_theme.items():
        if theme == "unclassified" and classified and len(group) < 3:
            continue
        themes.append({
            "theme": theme,
            "count": len(group),
            "tokens": [t.symbol for t in group[:8]],
            "total_volume": round(sum(t.vol_h24 for t in group)),
            "median_change": round(
                sorted(t.chg_h24 for t in group)[len(group) // 2], 1
            ),
            "avg_score": round(sum(t.score for t in group) / len(group), 1),
        })
    themes.sort(key=lambda x: (x["count"], x["total_volume"]), reverse=True)

    words = Counter()
    for t in tokens:
        words.update(set(_tokenize(f"{t.name} {t.symbol}")))
    keywords = [{"word": w, "n": n} for w, n in words.most_common(15) if n >= 2]

    return {"themes": themes, "keywords": keywords}


def fetch_metas() -> list[dict]:
    """Dexscreener metas, normalized and sorted by 6h acceleration."""
    raw = ds_trending_metas()
    out = []
    for m in raw:
        if not isinstance(m, dict):
            continue
        chg = m.get("marketCapChange") or {}
        out.append({
            "name": m.get("name") or m.get("slug") or "?",
            "slug": m.get("slug") or "",
            "description": (m.get("description") or "")[:200],
            "market_cap": m.get("marketCap") or 0,
            "volume": m.get("volume") or 0,
            "token_count": m.get("tokenCount") or 0,
            "chg_h1": _num(chg.get("h1")),
            "chg_h6": _num(chg.get("h6")),
            "chg_h24": _num(chg.get("h24")),
        })
    out.sort(key=lambda x: x["chg_h6"], reverse=True)
    return out


def _num(x) -> float:
    try:
        return round(float(x), 2)
    except (TypeError, ValueError):
        return 0.0


def emerging(metas: list[dict], history_dir: str | Path, days: int = 3) -> list[dict]:
    """Compare against previous snapshots: new entries and accelerations."""
    hist_dir = Path(history_dir)
    hist_dir.mkdir(parents=True, exist_ok=True)

    seen_before: dict[str, float] = {}
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    for f in sorted(hist_dir.glob("metas_*.json")):
        try:
            stamp = datetime.strptime(f.stem.replace("metas_", ""), "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
            if stamp < cutoff:
                continue
            for m in json.loads(f.read_text(encoding="utf-8")):
                seen_before[m["name"]] = max(
                    seen_before.get(m["name"], 0.0), m.get("market_cap", 0) or 0
                )
        except Exception:  # noqa: BLE001
            continue

    out = []
    for m in metas:
        prev = seen_before.get(m["name"])
        if prev is None:
            out.append({**m, "status": "new", "growth": None})
        elif prev > 0 and m["market_cap"] > prev * 1.4:
            out.append({
                **m, "status": "accelerating",
                "growth": round((m["market_cap"] / prev - 1) * 100, 1),
            })
    out.sort(key=lambda x: (x["status"] != "new", -x["chg_h6"]))
    return out[:8]


def snapshot(metas: list[dict], history_dir: str | Path) -> None:
    d = Path(history_dir)
    d.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    (d / f"metas_{today}.json").write_text(
        json.dumps(metas, ensure_ascii=False), encoding="utf-8"
    )


def build(tokens: list, history_dir: str | Path) -> dict:
    metas = fetch_metas()
    result = {
        "metas": metas[:12],
        "emerging": emerging(metas, history_dir),
        **cluster_runners(tokens),
    }
    snapshot(metas, history_dir)

    lead = result["themes"][0] if result["themes"] else None
    result["headline"] = (
        f"{lead['theme']} leads with {lead['count']} runners "
        f"and ${lead['total_volume']:,.0f} in volume"
        if lead else "no dominant theme today"
    )
    return result
