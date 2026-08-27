"""The calibration loop — the part that "improves over time".

The mechanism is deliberately transparent rather than magical:

  1. Every run archives its selections along with the price at call time.
  2. The next run picks up the selections that have matured (24h) and measures
     what they actually did.
  3. We correlate each score component against the realised return, then move
     the weights in small steps toward whatever worked.

Weights are bounded and the step is small: the model drifts toward the current
market regime instead of overreacting to one unusual day. Every decision stays
readable in data/calibration.json.
"""
from __future__ import annotations

import json
import logging
import statistics
from datetime import datetime, timezone
from pathlib import Path

from scoring import DEFAULT_WEIGHTS
from sources import ds_pairs_for_tokens

log = logging.getLogger("learn")

MIN_SAMPLE = 30          # no adjustment below this many observations
LEARNING_RATE = 0.06     # maximum step per run, in absolute weight
W_MIN, W_MAX = 0.05, 0.45
MATURITY_HOURS = 24


def _now() -> datetime:
    return datetime.now(timezone.utc)


def record_picks(tokens: list, history_dir: str | Path, run_label: str,
                 extra: dict | None = None) -> None:
    """Archive this session's calls.

    `extra` carries the evidence snapshot and the causal analysis per mint, so
    the next post-mortem can judge the call against what we actually knew at the
    time rather than against hindsight.
    """
    d = Path(history_dir)
    d.mkdir(parents=True, exist_ok=True)
    extra = extra or {}
    payload = {
        "recorded_at": _now().isoformat(),
        "run": run_label,
        "picks": [
            {
                "mint": t.mint,
                "symbol": t.symbol,
                "price_usd": t.price_usd,
                "mcap": t.mcap,
                "score": t.score,
                "parts": t.score_parts,
                "kol_buyers": t.kol_buyers,
                "wash_score": t.wash_score,
                "rug_score": t.rug_score,
                **extra.get(t.mint, {}),
            }
            for t in tokens[:20]
        ],
    }
    stamp = _now().strftime("%Y-%m-%d_%H%M")
    (d / f"picks_{stamp}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _pending(history_dir: Path) -> list[dict]:
    """Selections old enough to judge, not yet evaluated."""
    out = []
    for f in sorted(history_dir.glob("picks_*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if data.get("evaluated"):
            continue
        try:
            rec = datetime.fromisoformat(data["recorded_at"])
        except Exception:  # noqa: BLE001
            continue
        if (_now() - rec).total_seconds() / 3600 >= MATURITY_HOURS:
            out.append({"file": f, "data": data})
    return out


def evaluate_past(history_dir: str | Path) -> list[dict]:
    """Measure realised return on matured selections. Returns the observations."""
    d = Path(history_dir)
    d.mkdir(parents=True, exist_ok=True)
    pending = _pending(d)
    if not pending:
        return []

    mints = [p["mint"] for item in pending for p in item["data"]["picks"]]
    if not mints:
        return []

    current: dict[str, float] = {}
    for pair in ds_pairs_for_tokens(mints):
        mint = (pair.get("baseToken") or {}).get("address")
        try:
            price = float(pair.get("priceUsd") or 0)
        except (TypeError, ValueError):
            continue
        if mint and price > 0:
            current[mint] = max(current.get(mint, 0.0), price)

    observations: list[dict] = []
    for item in pending:
        for p in item["data"]["picks"]:
            entry, now_price = p.get("price_usd") or 0, current.get(p["mint"])
            if entry <= 0:
                continue
            # A token that has vanished from liquid pools counts as -95%, not as
            # missing data: its disappearance is the information.
            ret = ((now_price / entry) - 1) * 100 if now_price else -95.0
            observations.append({
                "mint": p["mint"], "symbol": p.get("symbol", "?"),
                "return_pct": round(ret, 1), "parts": p.get("parts", {}),
                "score": p.get("score", 0), "kol_buyers": p.get("kol_buyers", 0),
                "delisted": now_price is None,
            })
        item["data"]["evaluated"] = True
        item["file"].write_text(
            json.dumps(item["data"], ensure_ascii=False), encoding="utf-8"
        )

    log.info("%d selections evaluated", len(observations))
    return observations


def _corr(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 5 or len(set(xs)) < 2 or len(set(ys)) < 2:
        return 0.0
    try:
        return statistics.correlation(xs, ys)
    except statistics.StatisticsError:
        return 0.0


def load_state(path: str | Path) -> dict:
    p = Path(path)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {"weights": dict(DEFAULT_WEIGHTS), "observations": [], "runs": 0}


def update(state: dict, observations: list[dict], path: str | Path) -> dict:
    """Apply one calibration step and persist the state."""
    if observations:
        state["observations"] = (state.get("observations", []) + observations)[-600:]
    obs = state.get("observations", [])
    state["runs"] = state.get("runs", 0) + 1
    weights = {**DEFAULT_WEIGHTS, **state.get("weights", {})}

    if len(obs) >= MIN_SAMPLE:
        returns = [o["return_pct"] for o in obs]
        # Clamp returns: one isolated 50x must not dictate the model.
        returns = [max(min(r, 300.0), -100.0) for r in returns]
        corrs = {}
        for key in DEFAULT_WEIGHTS:
            xs = [float(o.get("parts", {}).get(key, 0) or 0) for o in obs]
            corrs[key] = _corr(xs, returns)

        spread = max(abs(c) for c in corrs.values()) or 1.0
        for key, c in corrs.items():
            weights[key] = min(max(weights[key] + LEARNING_RATE * (c / spread),
                                   W_MIN), W_MAX)
        total = sum(weights.values())
        weights = {k: round(v / total, 4) for k, v in weights.items()}
        state["correlations"] = {k: round(v, 3) for k, v in corrs.items()}
        state["calibrated"] = True
    else:
        state["calibrated"] = False

    state["weights"] = weights
    state["sample_size"] = len(obs)
    state["stats"] = performance(obs)
    state["updated_at"] = _now().isoformat()

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(state, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return state


def performance(obs: list[dict]) -> dict:
    """Honest statistics, shown in the report exactly as computed."""
    if not obs:
        return {"n": 0}
    rets = [o["return_pct"] for o in obs]
    kol = [o["return_pct"] for o in obs if o.get("kol_buyers", 0) >= 3]
    return {
        "n": len(obs),
        "median_return": round(statistics.median(rets), 1),
        "hit_rate": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1),
        "big_win_rate": round(sum(1 for r in rets if r > 100) / len(rets) * 100, 1),
        "rug_rate": round(sum(1 for o in obs if o.get("delisted")) / len(obs) * 100, 1),
        "kol_median": round(statistics.median(kol), 1) if len(kol) >= 5 else None,
        "kol_n": len(kol),
    }
