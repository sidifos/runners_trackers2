"""Post-mortem — what happened to the last calls, and what that teaches.

Runs at the start of every session, twice a day:

  1. Re-price every past pick that has matured. Market cap up = the call was
     good data. Market cap down = bad data, and the reason gets investigated.
  2. Diagnose each failure against the evidence recorded at call time.
  3. Mine the accumulated record for patterns that hold across many calls, and
     turn those into lessons.
  4. Feed the lessons back into the next synthesis prompt and propose concrete
     threshold changes.

The lessons are statistical, not anecdotal: a pattern is only promoted once it
has held over enough calls to be more than noise. Everything is written to
data/lessons.json in plain text so the reasoning stays auditable.
"""
from __future__ import annotations

import json
import logging
import statistics
from datetime import datetime, timezone
from pathlib import Path

from sources import ds_pairs_for_tokens
from synthesis import _call, _parse, api_key

log = logging.getLogger("postmortem")

MATURITY_HOURS = 20          # a bit under 24h so the twice-daily cadence lines up
MIN_SUPPORT = 6              # observations before a pattern becomes a lesson
UP_THRESHOLD = 10.0          # % market-cap change counting as a good call
DOWN_THRESHOLD = -20.0       # % below which the call counts as bad


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ re-pricing

def _matured(history_dir: Path) -> list[dict]:
    out = []
    for f in sorted(history_dir.glob("picks_*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if data.get("postmortem_done"):
            continue
        try:
            rec = datetime.fromisoformat(data["recorded_at"])
        except Exception:  # noqa: BLE001
            continue
        if (_now() - rec).total_seconds() / 3600 >= MATURITY_HOURS:
            out.append({"file": f, "data": data, "recorded": rec})
    return out


def review(history_dir: str | Path) -> list[dict]:
    """Re-price matured picks and classify each as good or bad data."""
    d = Path(history_dir)
    d.mkdir(parents=True, exist_ok=True)
    batches = _matured(d)
    if not batches:
        return []

    mints = [p["mint"] for b in batches for p in b["data"].get("picks", [])]
    if not mints:
        return []

    current: dict[str, dict] = {}
    for pair in ds_pairs_for_tokens(mints):
        mint = (pair.get("baseToken") or {}).get("address")
        if not mint:
            continue
        try:
            mcap = float(pair.get("marketCap") or 0)
            liq = float((pair.get("liquidity") or {}).get("usd") or 0)
        except (TypeError, ValueError):
            continue
        prev = current.get(mint)
        if prev is None or liq > prev["liquidity"]:
            current[mint] = {"mcap": mcap, "liquidity": liq}

    verdicts: list[dict] = []
    for b in batches:
        hours = round((_now() - b["recorded"]).total_seconds() / 3600, 1)
        for p in b["data"].get("picks", []):
            entry_mc = p.get("mcap") or 0
            now = current.get(p["mint"])
            if entry_mc <= 0:
                continue
            if now is None or now["liquidity"] < 3000:
                change, label = -95.0, "dead"
            else:
                change = (now["mcap"] / entry_mc - 1) * 100
                label = ("good" if change >= UP_THRESHOLD
                         else "bad" if change <= DOWN_THRESHOLD else "flat")
            verdicts.append({
                "mint": p["mint"], "symbol": p.get("symbol", "?"),
                "entry_mcap": entry_mc,
                "current_mcap": (now or {}).get("mcap", 0),
                "change_pct": round(change, 1),
                "label": label,
                "hours_elapsed": hours,
                "run": b["data"].get("run", ""),
                "score": p.get("score", 0),
                "features": p.get("features", {}),
                "analysis": p.get("analysis", {}),
            })
        b["data"]["postmortem_done"] = True
        b["file"].write_text(json.dumps(b["data"], ensure_ascii=False),
                             encoding="utf-8")

    good = sum(1 for v in verdicts if v["label"] == "good")
    bad = sum(1 for v in verdicts if v["label"] in ("bad", "dead"))
    log.info("reviewed %d past calls — %d good, %d bad", len(verdicts), good, bad)
    return verdicts


# ------------------------------------------------------------ failure analysis

def diagnose(verdict: dict, lessons: list[str]) -> str:
    """Explain one failure. Uses the model when available, rules otherwise."""
    f = verdict.get("features", {})
    a = verdict.get("analysis", {})

    if api_key():
        prompt = (
            "This tracker called the following token. It has since lost value. "
            "Explain, from the evidence recorded at call time, which signal "
            "should have warned us. Be concrete and self-critical. If the "
            "evidence genuinely did not contain a warning, say that plainly — "
            "not every loss is a mistake.\n\n"
            f"Called at market cap ${verdict['entry_mcap']:,.0f}, now "
            f"${verdict['current_mcap']:,.0f} "
            f"({verdict['change_pct']:+.0f}% over {verdict['hours_elapsed']}h).\n"
            f"Evidence at call time: {json.dumps(f, ensure_ascii=False)}\n"
            f"Our reasoning was: {json.dumps(a, ensure_ascii=False)}\n"
            + (f"\nExisting lessons:\n- " + "\n- ".join(lessons[:8])
               if lessons else "")
            + '\n\nReturn JSON only: {"why_ran": "<the diagnosis, 2 sentences>", '
              '"confidence": <0-100>, "narrative_tag": "<short label>", '
              '"primary_driver": "diagnosis", "main_risk": "<the missed signal, '
              'or \'none identifiable\'>", "evidence_gaps": "<one sentence>"}'
        )
        parsed = _parse(_call(prompt, max_tokens=500))
        if parsed:
            return parsed.get("why_ran", "")

    bits = []
    if f.get("insider_pct", 0) > 15:
        bits.append(f"{f['insider_pct']:.0f}% insider supply was already flagged")
    if not f.get("kol_buyers"):
        bits.append("no tracked wallet ever confirmed it")
    if f.get("wash_score", 0) > 30:
        bits.append(f"volume credibility was already marginal at {f['wash_score']:.0f}/100")
    if f.get("age_hours", 999) < 12:
        bits.append("it was called inside its first 12 hours")
    if f.get("vol_liq_ratio", 0) > 20:
        bits.append(f"volume ran at {f['vol_liq_ratio']:.0f}× liquidity")
    if a.get("primary_driver") == "paid_promotion":
        bits.append("the move rested on paid promotion rather than real demand")
    if a.get("primary_driver") == "unexplained":
        bits.append("we never identified a catalyst in the first place")
    if not bits:
        return ("No warning present in the recorded evidence — this one looks "
                "like ordinary market risk rather than a missed signal.")
    return "Warning signs that were present: " + "; ".join(bits) + "."


# ------------------------------------------------------------- pattern mining

def _rate(rows: list[dict]) -> float:
    if not rows:
        return 0.0
    return sum(1 for r in rows if r["label"] in ("bad", "dead")) / len(rows) * 100


def mine_patterns(history: list[dict]) -> list[dict]:
    """Find feature buckets whose failure rate departs from the baseline."""
    usable = [v for v in history if v.get("features")]
    if len(usable) < MIN_SUPPORT * 2:
        return []

    baseline = _rate(usable)
    rules = [
        ("insider_pct", lambda f: f.get("insider_pct", 0) >= 15,
         "tokens with 15%+ insider supply"),
        ("no_kol", lambda f: not f.get("kol_buyers"),
         "tokens no tracked wallet bought"),
        ("kol_3plus", lambda f: f.get("kol_buyers", 0) >= 3,
         "tokens 3+ tracked wallets bought"),
        ("young", lambda f: f.get("age_hours", 999) < 12,
         "tokens called inside their first 12 hours"),
        ("wash_marginal", lambda f: f.get("wash_score", 0) >= 30,
         "tokens whose volume credibility was already marginal"),
        ("top10_heavy", lambda f: f.get("top10_pct", 0) >= 40,
         "tokens where the top 10 hold 40%+"),
        ("no_socials", lambda f: not f.get("has_twitter"),
         "tokens with no X account"),
        ("boosted", lambda f: f.get("paid_boosts", 0) > 0,
         "tokens running paid Dexscreener boosts"),
        ("high_vol_liq", lambda f: f.get("vol_liq_ratio", 0) >= 20,
         "tokens trading at 20x+ volume/liquidity"),
        ("thin_liq", lambda f: f.get("liquidity", 0) < 60_000,
         "tokens with under $60k liquidity"),
    ]

    found = []
    for key, pred, label in rules:
        subset = [v for v in usable if pred(v["features"])]
        if len(subset) < MIN_SUPPORT:
            continue
        rate = _rate(subset)
        delta = rate - baseline
        if abs(delta) < 12:
            continue
        med = statistics.median([v["change_pct"] for v in subset])
        direction = "worse" if delta > 0 else "better"
        found.append({
            "key": key,
            "text": (f"{label} fail {rate:.0f}% of the time vs {baseline:.0f}% "
                     f"baseline ({direction}), median outcome {med:+.0f}% "
                     f"over {len(subset)} calls"),
            "support": len(subset),
            "failure_rate": round(rate, 1),
            "baseline": round(baseline, 1),
            "delta": round(delta, 1),
            "median_outcome": round(med, 1),
        })

    found.sort(key=lambda x: -abs(x["delta"]))
    return found


def suggest_config(patterns: list[dict], cfg: dict) -> dict:
    """Concrete threshold changes implied by the patterns. Bounded and reversible."""
    out: dict[str, dict] = {}
    for p in patterns:
        if p["delta"] < 12:
            continue
        if p["key"] == "insider_pct" and cfg["insider_reject_pct"] > 20:
            out["insider_reject_pct"] = {
                "from": cfg["insider_reject_pct"],
                "to": max(cfg["insider_reject_pct"] - 5, 20), "why": p["text"]}
        if p["key"] == "no_kol" and not cfg.get("require_kol"):
            out["require_kol"] = {"from": False, "to": True, "why": p["text"]}
        if p["key"] == "young" and cfg["min_age_hours"] < 12:
            out["min_age_hours"] = {
                "from": cfg["min_age_hours"],
                "to": min(cfg["min_age_hours"] + 3, 12), "why": p["text"]}
        if p["key"] == "wash_marginal" and cfg["wash_reject"] > 35:
            out["wash_reject"] = {
                "from": cfg["wash_reject"],
                "to": max(cfg["wash_reject"] - 5, 35), "why": p["text"]}
        if p["key"] == "thin_liq" and cfg["min_liquidity_usd"] < 60_000:
            out["min_liquidity_usd"] = {
                "from": cfg["min_liquidity_usd"],
                "to": min(cfg["min_liquidity_usd"] + 10_000, 60_000),
                "why": p["text"]}
    return out


# ------------------------------------------------------------------- state I/O

def load_state(path: str | Path) -> dict:
    p = Path(path)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {"verdicts": [], "patterns": [], "lessons": [], "reviews": 0}


def run(history_dir: str | Path, state_path: str | Path, cfg: dict) -> dict:
    """Full post-mortem pass. Returns the updated state."""
    state = load_state(state_path)
    lessons = [p["text"] for p in state.get("patterns", [])]

    verdicts = review(history_dir)
    for v in verdicts:
        if v["label"] in ("bad", "dead"):
            v["diagnosis"] = diagnose(v, lessons)

    state["verdicts"] = (state.get("verdicts", []) + verdicts)[-400:]
    state["reviews"] = state.get("reviews", 0) + 1
    state["patterns"] = mine_patterns(state["verdicts"])
    state["lessons"] = [p["text"] for p in state["patterns"]]
    state["suggested_config"] = suggest_config(state["patterns"], cfg)

    recent = state["verdicts"][-120:]
    if recent:
        state["scorecard"] = {
            "reviewed": len(recent),
            "good": sum(1 for v in recent if v["label"] == "good"),
            "flat": sum(1 for v in recent if v["label"] == "flat"),
            "bad": sum(1 for v in recent if v["label"] == "bad"),
            "dead": sum(1 for v in recent if v["label"] == "dead"),
            "median_change": round(
                statistics.median([v["change_pct"] for v in recent]), 1),
            "accuracy": round(
                sum(1 for v in recent if v["label"] == "good") / len(recent) * 100, 1),
        }
    state["updated_at"] = _now().isoformat()

    Path(state_path).parent.mkdir(parents=True, exist_ok=True)
    Path(state_path).write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    if state.get("patterns"):
        log.info("%d pattern(s) now supported by the record", len(state["patterns"]))
    return state


def recent_verdicts(state: dict, limit: int = 12) -> list[dict]:
    """Most recent reviewed calls, worst first — for the report."""
    vs = state.get("verdicts", [])[-40:]
    return sorted(vs, key=lambda v: v["change_pct"])[:limit]
