"""Causal evidence gathering — the "why did this run" layer.

A theme label like "animals" is not a reason. It describes what the token *is*,
not what happened to it. This module collects the observable facts that could
explain a move, so the synthesis step reasons over evidence instead of guessing:

  - launch context (pump.fun metadata, creator history, graduation timing)
  - attention (reply volume, boost spend, socials actually present)
  - tracked-wallet timeline (who bought, how early, how clustered)
  - the shape of the move itself (slow grind vs vertical, where it started)
  - holder structure (from insiders.py)

Everything is best-effort: any source can fail without breaking the run.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from sources import _get

log = logging.getLogger("research")

PF_BASES = [
    "https://frontend-api-v3.pump.fun",
    "https://frontend-api-v2.pump.fun",
    "https://frontend-api.pump.fun",
]
_working_base: str | None = None


def _pf(path: str, params: dict | None = None):
    """Try pump.fun bases in order, remember the one that answers."""
    global _working_base
    bases = [_working_base] if _working_base else PF_BASES
    for base in bases + [b for b in PF_BASES if b != _working_base]:
        if not base:
            continue
        data = _get("pf", base + path, params=params, tries=1, timeout=15)
        if data is not None:
            _working_base = base
            return data
    return None


def pump_metadata(mint: str) -> dict:
    """Launch metadata for a pump.fun token. Empty dict if not a pump launch."""
    data = _pf(f"/coins/{mint}")
    if not isinstance(data, dict):
        return {}
    return {
        "is_pump": True,
        "description": (data.get("description") or "")[:600],
        "twitter": data.get("twitter") or "",
        "telegram": data.get("telegram") or "",
        "website": data.get("website") or "",
        "created_timestamp": data.get("created_timestamp") or 0,
        "reply_count": int(data.get("reply_count") or 0),
        "graduated": bool(data.get("complete")),
        "king_of_hill": data.get("king_of_the_hill_timestamp") or 0,
        "creator": data.get("creator") or "",
        "pump_mcap_usd": data.get("usd_market_cap") or 0,
    }


def creator_history(creator: str) -> dict:
    """How many tokens this creator has launched — serial launchers are a signal."""
    if not creator:
        return {}
    data = _pf(f"/coins/user-created-coins/{creator}",
               params={"offset": 0, "limit": 50, "includeNsfw": "false"})
    if not isinstance(data, list):
        return {}
    graduated = sum(1 for c in data if isinstance(c, dict) and c.get("complete"))
    return {
        "creator_launches": len(data),
        "creator_graduated": graduated,
        "creator_grad_rate": round(graduated / len(data) * 100, 1) if data else 0.0,
    }


def move_shape(t) -> dict:
    """Classify the shape of the price move from the available windows.

    Distinguishes a sustained bid from a single vertical candle, and locates
    roughly when the move started. This is what separates "a KOL called it two
    hours ago" from "it has been grinding up all day".
    """
    h24, h6, h1, m5 = t.chg_h24, t.chg_h6, t.chg_h1, t.chg_m5
    shape, started = "unclear", "unknown"

    if h24 > 0:
        recent_share = max(h6, 0) / h24 if h24 else 0
        last_hour_share = max(h1, 0) / h24 if h24 else 0
        if last_hour_share > 0.6:
            shape, started = "vertical", "within the last hour"
        elif recent_share > 0.7:
            shape, started = "accelerating", "in the last 6 hours"
        elif recent_share < 0.25:
            shape, started = "front-loaded", "more than 6 hours ago"
        else:
            shape, started = "sustained grind", "spread across the day"

    cooling = h1 < 0 < h24
    return {
        "shape": shape,
        "move_started": started,
        "cooling_off": cooling,
        "vol_concentration_h6": round(t.vol_h6 / t.vol_h24, 2) if t.vol_h24 else 0,
        "buy_pressure_h1": (round(t.buys_h1 / (t.buys_h1 + t.sells_h1), 2)
                            if (t.buys_h1 + t.sells_h1) else None),
    }


def kol_timeline(t, buy_details: dict) -> dict:
    """Who bought, how early, and how tightly clustered."""
    hit = buy_details.get(t.mint) or {}
    times = sorted(hit.get("timestamps") or [])
    out = {
        "kol_buyers": t.kol_buyers,
        "kol_names": t.kol_names,
        "kol_tiers": hit.get("tiers", []),
        "cluster_minutes": None,
        "first_buy_ago_h": None,
    }
    if len(times) >= 2:
        out["cluster_minutes"] = round((times[-1] - times[0]) / 60, 1)
    if times:
        import time as _time
        out["first_buy_ago_h"] = round((_time.time() - times[0]) / 3600, 1)
    return out


def attention_signals(t, pump: dict) -> dict:
    """Proxies for real attention, as opposed to volume."""
    replies = pump.get("reply_count", 0)
    return {
        "pump_replies": replies,
        "replies_per_hour": (round(replies / max(t.age_hours, 1), 1)
                             if replies else 0),
        "paid_boosts": t.boosted,
        "has_twitter": bool(t.socials.get("twitter") or t.socials.get("x")
                            or pump.get("twitter")),
        "has_telegram": bool(t.socials.get("telegram") or pump.get("telegram")),
        "has_website": bool(t.websites or pump.get("website")),
        "has_github": any("github" in w.lower() for w in t.websites),
        "twitter_handle": (t.socials.get("twitter") or t.socials.get("x")
                           or pump.get("twitter") or ""),
    }


def build_evidence(t, holder_profile, buy_details: dict,
                   metas: list[dict]) -> dict:
    """Assemble everything known about one runner into a single bundle."""
    pump = pump_metadata(t.mint)
    creator = creator_history(pump.get("creator", "")) if pump else {}

    matching_metas = []
    blob = f"{t.name} {t.symbol} {t.description} {pump.get('description','')}".lower()
    for m in metas[:12]:
        name = (m.get("name") or "").lower()
        if name and any(w in blob for w in name.split() if len(w) > 3):
            matching_metas.append({"name": m["name"], "chg_h6": m.get("chg_h6", 0)})

    return {
        "symbol": t.symbol,
        "name": t.name,
        "mint": t.mint,
        "age_hours": round(t.age_hours, 1),
        "market": {
            "mcap": t.mcap, "liquidity": t.liquidity, "vol_h24": t.vol_h24,
            "chg_h24": t.chg_h24, "chg_h6": t.chg_h6, "chg_h1": t.chg_h1,
            "txns_h24": t.txns_h24, "avg_trade_usd": round(t.avg_trade, 1),
            "vol_liq_ratio": round(t.vol_liq, 1),
        },
        "launch": {
            "description": pump.get("description") or t.description,
            "is_pump_launch": bool(pump),
            "graduated": pump.get("graduated"),
            "reached_king_of_hill": bool(pump.get("king_of_hill")),
            **creator,
        },
        "attention": attention_signals(t, pump),
        "shape": move_shape(t),
        "kol": kol_timeline(t, buy_details),
        "holders": holder_profile.as_dict() if holder_profile else {},
        "quality_flags": {
            "wash_score": round(t.wash_score),
            "wash_reasons": t.wash_flags,
            "rug_score": round(t.rug_score),
            "rug_reasons": t.rug_flags,
        },
        "matching_metas": matching_metas,
    }


def gather(tokens: list, holder_profiles: dict, buy_details: dict,
           metas: list[dict], max_workers: int = 4) -> dict[str, dict]:
    """Evidence bundles for every runner, gathered concurrently."""
    log.info("gathering causal evidence for %d runners…", len(tokens))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        bundles = list(pool.map(
            lambda t: build_evidence(t, holder_profiles.get(t.mint),
                                     buy_details, metas),
            tokens,
        ))
    return {t.mint: b for t, b in zip(tokens, bundles)}
