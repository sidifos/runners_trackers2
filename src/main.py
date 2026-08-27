"""Orchestrator — one full tracker run.

    python src/main.py --run morning
    python src/main.py --run evening --top 15
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import yaml  # noqa: E402

import filters  # noqa: E402
import kol  # noqa: E402
import learn  # noqa: E402
import narrative  # noqa: E402
import scoring  # noqa: E402
import sources  # noqa: E402
from report import render  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
HISTORY = DATA / "history"
OUT = ROOT / "out"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-9s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("main")


def load_config() -> dict:
    with (ROOT / "config" / "settings.yaml").open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def gather_candidates(cfg: dict) -> tuple[list[str], dict[str, dict]]:
    """Assemble today's universe from several independent sources."""
    log.info("collecting candidates…")
    meta: dict[str, dict] = {}

    trending = sources.gt_trending_pools()
    new_pools = sources.gt_new_pools()
    log.info("  GeckoTerminal: %d trending, %d new pools",
             len(trending), len(new_pools))

    mints = sources.gt_pool_mints(trending) + sources.gt_pool_mints(new_pools)

    boosts = sources.ds_boosted_tokens()
    for b in boosts:
        addr = b.get("tokenAddress")
        if addr:
            mints.append(addr)
            meta[addr] = {"description": (b.get("description") or "")[:400]}
    log.info("  Dexscreener: %d boosted tokens", len(boosts))

    profiles = sources.ds_token_profiles()
    for p in profiles:
        addr = p.get("tokenAddress")
        if addr:
            mints.append(addr)
            meta.setdefault(addr, {})["description"] = (p.get("description") or "")[:400]
    log.info("  Dexscreener: %d recent profiles", len(profiles))

    seen: set[str] = set()
    uniq = [m for m in mints if m not in sources.STABLES
            and not (m in seen or seen.add(m))]
    log.info("  → %d unique mints", len(uniq))
    return uniq[: cfg["max_candidates"]], meta


def enrich(mints: list[str], meta: dict[str, dict],
           cfg: dict) -> list[filters.Token]:
    log.info("enriching from Dexscreener…")
    pairs = sources.ds_pairs_for_tokens(mints)
    tokens = list(filters.dedupe_best_pair(pairs).values())
    log.info("  %d tokens with market data", len(tokens))

    for t in tokens:
        t.description = meta.get(t.mint, {}).get("description", "")

    # Cheap pre-filter before calling RugCheck (one request per token).
    pre = [
        t for t in tokens
        if t.liquidity >= cfg["min_liquidity_usd"] * 0.7
        and t.vol_h24 >= cfg["min_volume_usd"] * 0.7
        and t.chg_h24 >= cfg["min_change_pct"] * 0.6
    ]
    log.info("  %d tokens clear the pre-filter", len(pre))

    for t in pre:
        filters.score_wash(t, cfg)

    plausible = [t for t in pre if t.wash_score < cfg["wash_reject"] + 20]
    log.info("running RugCheck on %d tokens…", len(plausible))
    with ThreadPoolExecutor(max_workers=4) as pool:
        reports = list(pool.map(lambda t: sources.rugcheck_summary(t.mint), plausible))
    for t, rep in zip(plausible, reports):
        filters.score_rug(t, rep, cfg)

    return plausible


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="morning", choices=["morning", "evening"])
    ap.add_argument("--top", type=int, default=None)
    ap.add_argument("--no-learn", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    if args.top:
        cfg["top_n"] = args.top
    for d in (DATA, HISTORY, OUT):
        d.mkdir(parents=True, exist_ok=True)

    started = datetime.now(timezone.utc)
    log.info("=== run %s — %s UTC ===", args.run, started.strftime("%Y-%m-%d %H:%M"))

    # 1. Calibrate against previous selections that have matured.
    state = learn.load_state(DATA / "calibration.json")
    if not args.no_learn:
        try:
            observations = learn.evaluate_past(HISTORY)
            state = learn.update(state, observations, DATA / "calibration.json")
            log.info("active weights: %s", state["weights"])
        except Exception as e:  # noqa: BLE001
            log.warning("calibration skipped: %s", e)

    # 2. Today's universe.
    mints, meta = gather_candidates(cfg)
    if not mints:
        log.error("no candidates retrieved — sources unavailable?")
        return 1
    tokens = enrich(mints, meta, cfg)

    # 3. Tracked-wallet confluence.
    wallets = kol.load_wallets(ROOT / "config" / "kol_wallets.csv")
    if wallets and sources.helius_key():
        log.info("analysing %d tracked wallets…", len(wallets))
        buys = kol.collect_buys(wallets, cfg["kol_window_hours"])
        kol.apply_to_tokens(tokens, buys)
        hits = sum(1 for t in tokens if t.kol_buyers)
        log.info("  %d tokens touched by at least one tracked wallet", hits)
    else:
        log.info("KOL confluence inactive (wallet list or Helius key missing)")

    # 4. Gates and ranking.
    kept, rejected = [], []
    for t in tokens:
        ok, why = filters.passes_gates(t, cfg)
        (kept if ok else rejected).append(t if ok else (t, why))
    log.info("%d kept, %d cut", len(kept), len(rejected))

    ranked = scoring.rank(kept, cfg, state.get("weights"))
    runners = ranked[: cfg["top_n"]]

    # 5. Narratives.
    narr = narrative.build(runners, HISTORY)
    log.info("narrative: %s", narr["headline"])

    # 6. Archive for the next run's calibration.
    learn.record_picks(runners, HISTORY, args.run)

    # 7. Report.
    payload = {
        "generated_at": started,
        "run": args.run,
        "runners": runners,
        "narrative": narr,
        "rejected": [
            {"symbol": t.symbol, "reason": why,
             "wash": round(t.wash_score), "rug": round(t.rug_score),
             "chg": round(t.chg_h24, 1), "vol": t.vol_h24}
            for t, why in sorted(rejected, key=lambda x: -x[0].vol_h24)[:25]
        ],
        "stats": {
            "scanned": len(mints),
            "analyzed": len(tokens),
            "kept": len(kept),
            "rejected": len(rejected),
            "kol_active": bool(wallets and sources.helius_key()),
            "wallets_tracked": len(wallets),
        },
        "calibration": state,
    }
    html_path = OUT / "index.html"
    render(payload, html_path)
    (OUT / "latest.json").write_text(
        json.dumps(
            {
                "generated_at": started.isoformat(),
                "run": args.run,
                "headline": narr["headline"],
                "runners": [
                    {
                        "rank": i, "symbol": t.symbol, "name": t.name, "mint": t.mint,
                        "score": t.score, "chg_h24": t.chg_h24, "vol_h24": t.vol_h24,
                        "liquidity": t.liquidity, "mcap": t.mcap,
                        "wash_score": t.wash_score, "rug_score": t.rug_score,
                        "kol_buyers": t.kol_buyers, "url": t.url,
                        "theme": narrative.classify_theme(t),
                    }
                    for i, t in enumerate(runners, 1)
                ],
                "stats": payload["stats"],
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    log.info("report written → %s", html_path)

    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(f"### {len(runners)} runners — {narr['headline']}\n\n")
            for i, t in enumerate(runners[:10], 1):
                fh.write(f"{i}. **{t.symbol}** — score {t.score} · "
                         f"{t.chg_h24:+.0f}% · vol ${t.vol_h24:,.0f}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
