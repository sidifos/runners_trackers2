"""Measuring the wallets, so the tiers stop being opinions.

The tiers shipped in kol_wallets.csv are priors — reputation, nothing more. This
module replaces them with measurement as evidence accumulates.

The metric that matters for a tracker is not PnL and it is not win rate. It is
**Early Alpha Rate**: the share of a wallet's entries where the token went on to
multiply *after* they bought. A wallet that enters at $300k on a token that
reaches $6M is worth following. A wallet that enters at $4.5M on the same token
made money and is worth nothing to us.

    early_alpha = peak market cap observed after entry / market cap at entry

Peaks are built up observation by observation across runs, so the numbers get
more accurate the longer the tracker runs. Nothing is back-filled or assumed.
"""
from __future__ import annotations

import json
import logging
import statistics
from datetime import datetime, timezone
from pathlib import Path

from sources import ds_pairs_for_tokens

log = logging.getLogger("kol_scoring")

MIN_CALLS = 8            # entries before a wallet is scored on data
MIN_CALLS_RETIER = 15    # entries before data overrides the seed tier
TRACK_WINDOW_DAYS = 45


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


def load(path: str | Path) -> dict:
    p = Path(path)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {"trades": {}, "scores": {}, "updated_at": None}


def save(state: dict, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(state, ensure_ascii=False, indent=1),
                          encoding="utf-8")


def record_entries(state: dict, buy_details: dict, tokens: list) -> int:
    """Log each wallet's first observed entry into a mint, with the mcap then."""
    mcaps = {t.mint: t.mcap for t in tokens if t.mcap > 0}
    added = 0
    for mint, hit in buy_details.items():
        entry_mc = mcaps.get(mint)
        if not entry_mc:
            continue
        for label in hit.get("labels", []):
            key = f"{label}|{mint}"
            if key in state["trades"]:
                continue
            state["trades"][key] = {
                "kol": label, "mint": mint,
                "entry_mcap": entry_mc, "entry_ts": _now(),
                "peak_mcap": entry_mc, "last_mcap": entry_mc,
                "last_ts": _now(),
            }
            added += 1
    if added:
        log.info("logged %d new tracked-wallet entries", added)
    return added


def update_peaks(state: dict) -> int:
    """Re-price tracked mints and raise the observed peak where applicable."""
    cutoff = _now() - TRACK_WINDOW_DAYS * 86400
    live = {k: v for k, v in state["trades"].items() if v["entry_ts"] >= cutoff}
    state["trades"] = live
    mints = sorted({v["mint"] for v in live.values()})
    if not mints:
        return 0

    current: dict[str, float] = {}
    for pair in ds_pairs_for_tokens(mints):
        mint = (pair.get("baseToken") or {}).get("address")
        try:
            mc = float(pair.get("marketCap") or 0)
        except (TypeError, ValueError):
            continue
        if mint and mc > 0:
            current[mint] = max(current.get(mint, 0.0), mc)

    touched = 0
    for v in live.values():
        mc = current.get(v["mint"])
        if mc is None:
            v["last_mcap"] = 0.0        # no liquid pool left
            v["last_ts"] = _now()
            continue
        if mc > v["peak_mcap"]:
            v["peak_mcap"] = mc
            touched += 1
        v["last_mcap"] = mc
        v["last_ts"] = _now()
    return touched


def compute(state: dict) -> dict:
    """Per-wallet metrics from the accumulated entries."""
    by_kol: dict[str, list[dict]] = {}
    for v in state["trades"].values():
        by_kol.setdefault(v["kol"], []).append(v)

    scores = {}
    for kol, trades in by_kol.items():
        mults = [t["peak_mcap"] / t["entry_mcap"]
                 for t in trades if t["entry_mcap"] > 0]
        if not mults:
            continue
        n = len(mults)
        held = [t["last_mcap"] / t["entry_mcap"]
                for t in trades if t["entry_mcap"] > 0]
        scores[kol] = {
            "calls": n,
            "early_alpha_2x": round(sum(1 for m in mults if m >= 2) / n * 100, 1),
            "early_alpha_3x": round(sum(1 for m in mults if m >= 3) / n * 100, 1),
            "early_alpha_5x": round(sum(1 for m in mults if m >= 5) / n * 100, 1),
            "early_alpha_10x": round(sum(1 for m in mults if m >= 10) / n * 100, 1),
            "median_multiple": round(statistics.median(mults), 2),
            "median_entry_mcap": round(statistics.median(
                [t["entry_mcap"] for t in trades])),
            "still_up_rate": round(sum(1 for h in held if h >= 1) / n * 100, 1)
            if held else 0.0,
            "dead_rate": round(sum(1 for h in held if h < 0.1) / n * 100, 1)
            if held else 0.0,
            "measured": n >= MIN_CALLS,
        }
    state["scores"] = scores
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    return scores


SEED_VALUE = {"S": 80.0, "A": 62.0, "B": 45.0, "C": 30.0}


def tracker_value(score: dict | None, seed_tier: str) -> tuple[float, str]:
    """Blend measurement with the seed prior, shifting to data as it accumulates.

    Returns (value 0-100, basis) where basis explains what the number rests on.
    """
    prior = SEED_VALUE.get(seed_tier.upper(), 40.0)
    if not score or score["calls"] < MIN_CALLS:
        return prior, "reputation prior"

    # Earliness dominates: a wallet that repeatedly enters before a 2x is the
    # asset. Consistency and survival act as brakes on one-lucky-trade wallets.
    measured = (
        score["early_alpha_2x"] * 0.40
        + score["early_alpha_3x"] * 0.25
        + score["early_alpha_5x"] * 0.15
        + score["still_up_rate"] * 0.10
        + max(0.0, 100 - score["dead_rate"] * 2) * 0.10
    )
    # Weight of evidence rises with sample size. The prior exists to cover the
    # absence of data, not to outvote it: once a wallet has a real record, that
    # record has to be able to demote a famous name and promote an unknown one.
    w = min(0.30 + (score["calls"] - MIN_CALLS) * 0.04, 0.90)
    value = prior * (1 - w) + measured * w
    basis = (f"{score['calls']} measured entries"
             if score["calls"] >= MIN_CALLS_RETIER
             else f"{score['calls']} entries, prior still dominant")
    return round(value, 1), basis


def retier(wallets: list[dict], scores: dict) -> list[dict]:
    """Recompute S/A/B/C from tracker value. Seed tiers hold until data earns it."""
    enriched = []
    for w in wallets:
        s = scores.get(w["label"])
        value, basis = tracker_value(s, w.get("tier", "C"))
        enriched.append({**w, "value": value, "basis": basis, "metrics": s})

    enriched.sort(key=lambda x: -x["value"])
    n = len(enriched)
    cuts = [max(int(n * 0.12), 1), max(int(n * 0.37), 2), max(int(n * 0.72), 3)]
    for i, w in enumerate(enriched):
        new = "S" if i < cuts[0] else "A" if i < cuts[1] else "B" if i < cuts[2] else "C"
        w["measured_tier"] = new
        w["tier_changed"] = new != w.get("tier", "C")
        w["weight"] = {"S": 3.0, "A": 2.0, "B": 1.4, "C": 1.0}[new]
    changed = sum(1 for w in enriched if w["tier_changed"])
    if changed:
        log.info("%d wallet(s) re-tiered from measurement", changed)
    return enriched


def run(wallets: list[dict], buy_details: dict, tokens: list,
        path: str | Path) -> tuple[list[dict], dict]:
    """One full scoring pass. Returns (re-tiered wallets, state)."""
    state = load(path)
    record_entries(state, buy_details, tokens)
    update_peaks(state)
    scores = compute(state)
    ranked = retier(wallets, scores)
    state["ranking"] = [
        {"kol": w["label"], "tier": w["measured_tier"], "seed_tier": w.get("tier"),
         "value": w["value"], "basis": w["basis"], "calls": (w.get("metrics") or {}).get("calls", 0)}
        for w in ranked
    ]
    save(state, path)
    return ranked, state
