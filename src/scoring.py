"""Runner ranking.

The score combines five components normalised 0-100, weighted by weights that
self-calibrate (see learn.py). KOL confluence is treated as a multiplier rather
than a component: a token can surface without it, but with it the token climbs
hard — it is the single hardest signal on the market to fake.
"""
from __future__ import annotations

import math

from filters import Token

DEFAULT_WEIGHTS = {
    "momentum": 0.30,
    "volume": 0.25,
    "liquidity": 0.15,
    "social": 0.10,
    "freshness": 0.20,
}


def _log_norm(x: float, floor: float, ceil: float) -> float:
    """Normalise on a log scale — orders of magnitude matter, units do not."""
    if x <= floor:
        return 0.0
    if x >= ceil:
        return 100.0
    lo, hi = math.log10(floor), math.log10(ceil)
    return (math.log10(x) - lo) / (hi - lo) * 100.0


def momentum_score(t: Token) -> float:
    """Weight the windows: a run that holds over 6h beats a 5-minute spike."""
    parts = [
        (t.chg_h24, 0.40, 400.0),
        (t.chg_h6, 0.35, 200.0),
        (t.chg_h1, 0.25, 100.0),
    ]
    total = 0.0
    for change, weight, cap in parts:
        total += weight * max(0.0, min(change, cap)) / cap * 100.0
    # Penalise a token that has already given back most of the move in the last hour.
    if t.chg_h24 > 50 and t.chg_h1 < -20:
        total *= 0.6
    return min(total, 100.0)


def volume_score(t: Token) -> float:
    """Raw volume, discounted by how suspicious it looks."""
    raw = _log_norm(t.vol_h24, 20_000, 20_000_000)
    return raw * (1.0 - t.wash_score / 100.0 * 0.8)


def liquidity_score(t: Token) -> float:
    """Pool depth plus the health of the liquidity/mcap ratio."""
    depth = _log_norm(t.liquidity, 15_000, 3_000_000)
    ratio = t.liq_mcap
    health = 100.0 if 0.03 <= ratio <= 0.5 else (50.0 if ratio > 0.005 else 0.0)
    return depth * 0.7 + health * 0.3


def social_score(t: Token) -> float:
    """Real social footprint: a runner with nothing attached does not last."""
    s = 0.0
    if "twitter" in t.socials or "x" in t.socials:
        s += 45
    if t.websites:
        s += 20
    if "telegram" in t.socials:
        s += 15
    if any("github" in w.lower() for w in t.websites):
        s += 15
    if t.boosted:
        s += 5
    return min(s, 100.0)


def freshness_score(t: Token) -> float:
    """The window a KOL can actually work with: not too early, not distributed yet."""
    h = t.age_hours
    if h < 6:
        return 60.0          # still fragile, often unverifiable
    if h <= 72:
        return 100.0         # the sweet spot
    if h <= 24 * 14:
        return 70.0
    if h <= 24 * 60:
        return 40.0
    return 20.0


def kol_multiplier(t: Token, cfg: dict) -> float:
    """Tracked-wallet confluence. Capped so it cannot swamp everything else."""
    if not t.kol_buyers:
        return 1.0
    n = t.kol_weight or t.kol_buyers
    boost = min(math.log1p(n) / math.log1p(cfg["kol_saturation"]), 1.0)
    return 1.0 + boost * (cfg["kol_max_multiplier"] - 1.0)


def score_token(t: Token, cfg: dict, weights: dict | None = None) -> None:
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    parts = {
        "momentum": momentum_score(t),
        "volume": volume_score(t),
        "liquidity": liquidity_score(t),
        "social": social_score(t),
        "freshness": freshness_score(t),
    }
    base = sum(parts[k] * w[k] for k in parts)
    mult = kol_multiplier(t, cfg)
    # Trust modulates the final result rather than being a component: a dubious
    # token must not be able to compensate with raw momentum.
    trust = 1.0 - (t.wash_score * 0.006 + t.rug_score * 0.004)
    t.score = round(max(base * mult * max(trust, 0.15), 0.0), 2)
    parts["kol_multiplier"] = round(mult, 3)
    parts["trust_factor"] = round(trust, 3)
    t.score_parts = {k: round(v, 1) for k, v in parts.items()}


def rank(tokens: list[Token], cfg: dict, weights: dict | None = None) -> list[Token]:
    for t in tokens:
        score_token(t, cfg, weights)
    # Require KOL confluence when the wallet list is loaded and strict mode is on.
    if cfg.get("require_kol") and any(t.kol_buyers for t in tokens):
        tokens = [t for t in tokens if t.kol_buyers >= cfg["min_kol_buyers"]]
    return sorted(tokens, key=lambda x: x.score, reverse=True)
