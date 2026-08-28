"""Orchestrator — one full tracker run.

    python src/main.py --run morning
    python src/main.py --run evening --top 15

Order matters. The post-mortem runs first so that what the market taught us
since the last session is already in hand before anything new is judged.
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
import insiders  # noqa: E402
import kol  # noqa: E402
import kol_scoring  # noqa: E402
import learn  # noqa: E402
import narrative  # noqa: E402
import postmortem  # noqa: E402
import research  # noqa: E402
import scoring  # noqa: E402
import sources  # noqa: E402
import synthesis  # noqa: E402
from report import render  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
HISTORY = DATA / "history"
OUT = ROOT / "out"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-11s %(message)s",
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

    # The ceiling is as much a part of the definition as the floor. $FARTCOIN at
    # +10% is a major re-rating, not a runner: nobody follows a Solana tracker to
    # be told a $2B coin moved a tenth. Letting them into the scan burns API
    # calls and, worse, fills the published cut list with names that were never
    # candidates — which reads as a tracker that does not know what it is for.
    ceiling = cfg.get("max_mcap_usd") or 0
    pre = [
        t for t in tokens
        if t.liquidity >= cfg["min_liquidity_usd"] * 0.7
        and t.vol_h24 >= cfg["min_volume_usd"] * 0.7
        and t.chg_h24 >= cfg["min_change_pct"] * 0.6
        and (not ceiling or t.mcap <= ceiling)
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


def holder_pass(tokens: list, cfg: dict) -> dict:
    """Full holder profiles for tokens still in contention."""
    shortlist = [t for t in tokens if t.rug_score < cfg["rug_reject"]
                 and t.wash_score < cfg["wash_reject"]]
    log.info("pulling holder structure for %d tokens…", len(shortlist))
    with ThreadPoolExecutor(max_workers=3) as pool:
        profiles = list(pool.map(lambda t: insiders.fetch_profile(t.mint), shortlist))
    out = {}
    for t, p in zip(shortlist, profiles):
        out[t.mint] = p
        t.holders = p.as_dict()
    flagged = sum(1 for p in profiles if p.insider_pct >= cfg["insider_needs_kol_pct"])
    log.info("  %d token(s) carry elevated insider supply", flagged)

    # Bundled launches and sniper clusters are the dominant scam shape on Solana
    # right now. A batch of memecoins that returns *zero* insider supply and zero
    # bundles is not a clean batch, it is a dead signal — RugCheck not answering,
    # a renamed field, a launchpad it does not cover yet. Silence has to be
    # audible, otherwise the report prints an empty insider column and the reader
    # takes it for an all-clear.
    live = [p for p in profiles if p.available]
    signal_live = any(p.insider_pct > 0 or p.bundled for p in live)
    if len(live) >= 8 and not signal_live:
        log.warning("insider detection returned nothing across %d tokens — "
                    "treat the insider column as unavailable, not as clean",
                    len(live))
    unreadable = sum(1 for p in live if not p.reliable)
    if unreadable:
        log.warning("%d holder report(s) unreadable — concentration not judged "
                    "for those tokens", unreadable)
    return out


def features_of(t, evidence: dict | None) -> dict:
    """The snapshot the next post-mortem will judge this call against."""
    ev = evidence or {}
    att = ev.get("attention", {})
    return {
        "mcap": t.mcap, "liquidity": t.liquidity, "vol_h24": t.vol_h24,
        "chg_h24": t.chg_h24, "age_hours": round(t.age_hours, 1),
        "vol_liq_ratio": round(t.vol_liq, 1),
        "wash_score": round(t.wash_score), "rug_score": round(t.rug_score),
        "kol_buyers": t.kol_buyers, "kol_weight": t.kol_weight,
        "insider_pct": t.holders.get("insider_pct", 0),
        "top10_pct": t.holders.get("top10_pct", 0),
        "holder_count": t.holders.get("holder_count", 0),
        "has_twitter": att.get("has_twitter", bool(t.socials.get("twitter"))),
        "paid_boosts": t.boosted,
        "pump_replies": att.get("pump_replies", 0),
        "score": t.score,
    }


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

    # 1. Post-mortem: what happened to the last calls, and what it teaches.
    pm = {"lessons": [], "patterns": [], "verdicts": []}
    if not args.no_learn:
        try:
            pm = postmortem.run(HISTORY, DATA / "lessons.json", cfg)
        except Exception as e:  # noqa: BLE001
            log.warning("post-mortem skipped: %s", e)
    lessons = pm.get("lessons", [])
    if lessons:
        log.info("carrying %d lesson(s) into this run", len(lessons))
    if cfg.get("auto_apply_lessons"):
        for key, change in (pm.get("suggested_config") or {}).items():
            cfg[key] = change["to"]
            log.info("auto-applied %s: %s → %s", key, change["from"], change["to"])

    # 2. Weight calibration from realised returns.
    state = learn.load_state(DATA / "calibration.json")
    if not args.no_learn:
        try:
            state = learn.update(state, learn.evaluate_past(HISTORY),
                                 DATA / "calibration.json")
        except Exception as e:  # noqa: BLE001
            log.warning("calibration skipped: %s", e)

    # 3. Today's universe.
    mints, meta = gather_candidates(cfg)
    if not mints:
        log.error("no candidates retrieved — sources unavailable?")
        return 1
    tokens = enrich(mints, meta, cfg)

    # 4. Tracked-wallet confluence.
    wallets = kol.load_wallets(ROOT / "config" / "kol_wallets.csv")
    buy_details: dict = {}
    ranked_wallets: list[dict] = []
    if wallets and sources.helius_key():
        log.info("analysing %d tracked wallets…", len(wallets))
        buy_details = kol.collect_buys(wallets, cfg["kol_window_hours"])
        kol.apply_to_tokens(tokens, buy_details)
        for t in tokens:
            hit = buy_details.get(t.mint)
            if hit:
                t.kol_tiers = hit.get("tiers", [])
        log.info("  %d tokens touched by at least one tracked wallet",
                 sum(1 for t in tokens if t.kol_buyers))
        try:
            ranked_wallets, _ = kol_scoring.run(
                wallets, buy_details, tokens, DATA / "kol_scores.json")
        except Exception as e:  # noqa: BLE001
            log.warning("wallet scoring skipped: %s", e)
    else:
        log.info("KOL confluence inactive (wallet list or Helius key missing)")

    # 5. Holder structure, then the gates.
    profiles = holder_pass(tokens, cfg)

    kept, rejected = [], []
    for t in tokens:
        ok, why = filters.passes_gates(t, cfg)
        if ok and t.mint in profiles:
            ok, why = insiders.apply_gates(t, profiles[t.mint], cfg)
        (kept if ok else rejected).append(t if ok else (t, why))
    log.info("%d kept, %d cut", len(kept), len(rejected))

    ranked = scoring.rank(kept, cfg, state.get("weights"))
    runners = ranked[: cfg["top_n"]]

    # 6. Why did each of these run.
    evidence: dict = {}
    analyses: dict = {}
    if cfg.get("research_enabled") and runners:
        try:
            metas_now = narrative.fetch_metas()
            evidence = research.gather(
                runners[: cfg["research_top_n"]], profiles, buy_details, metas_now)
            if cfg.get("llm_synthesis"):
                analyses = synthesis.analyse(evidence, lessons)
                for t in runners:
                    t.analysis = analyses.get(t.mint, {})
        except Exception as e:  # noqa: BLE001
            log.warning("research pass degraded: %s", e)

    # 7. Narrative layer.
    narr = narrative.build(runners, HISTORY, scanned=tokens)
    for k in narr.get("knockoffs", []):
        log.info("knockoff wave: $%s copied by %s", k["origin"],
                 ", ".join("$" + m for m in k["members"][1:]))
    try:
        narr["daily"] = synthesis.daily_narrative(
            evidence, analyses, narr.get("metas", []), lessons)
        if narr["daily"].get("summary"):
            narr["headline"] = narr["daily"]["summary"]
    except Exception as e:  # noqa: BLE001
        log.warning("daily synthesis skipped: %s", e)
    log.info("narrative: %s", narr["headline"][:120])

    # 8. Archive this session's calls with everything needed to judge them later.
    learn.record_picks(
        runners, HISTORY, args.run,
        extra={t.mint: {"features": features_of(t, evidence.get(t.mint)),
                        "analysis": t.analysis} for t in runners},
    )

    # 9. Report.
    payload = {
        "generated_at": started,
        "run": args.run,
        "runners": runners,
        "narrative": narr,
        "evidence": evidence,
        "postmortem": pm,
        "wallet_ranking": ranked_wallets[:25],
        "rejected": [
            {"symbol": t.symbol, "reason": why,
             "wash": round(t.wash_score), "rug": round(t.rug_score),
             "insiders": t.holders.get("insider_pct", 0),
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
            "llm_active": bool(synthesis.api_key() and cfg.get("llm_synthesis")),
            "researched": len(evidence),
            # An empty insider column has to say which kind of empty it is.
            "insider_signal": bool(
                any(p.insider_pct > 0 or p.bundled for p in profiles.values())
                or len([p for p in profiles.values() if p.available]) < 8),
            "holders_unreadable": sum(
                1 for p in profiles.values() if p.available and not p.reliable),
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
                "daily": narr.get("daily", {}),
                "runners": [
                    {
                        "rank": i, "symbol": t.symbol, "name": t.name, "mint": t.mint,
                        "score": t.score, "chg_h24": t.chg_h24, "vol_h24": t.vol_h24,
                        "liquidity": t.liquidity, "mcap": t.mcap,
                        "wash_score": t.wash_score, "rug_score": t.rug_score,
                        "insider_pct": t.holders.get("insider_pct", 0),
                        "kol_buyers": t.kol_buyers, "kol_names": t.kol_names,
                        "url": t.url, "why_ran": t.analysis.get("why_ran", ""),
                        "narrative_tag": t.analysis.get("narrative_tag", ""),
                        "primary_driver": t.analysis.get("primary_driver", ""),
                        "confidence": t.analysis.get("confidence"),
                        "main_risk": t.analysis.get("main_risk", ""),
                    }
                    for i, t in enumerate(runners, 1)
                ],
                "lessons": lessons,
                "scorecard": pm.get("scorecard", {}),
                "stats": payload["stats"],
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    log.info("report written → %s", html_path)

    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(f"### {len(runners)} runners\n\n{narr['headline']}\n\n")
            for i, t in enumerate(runners[:10], 1):
                tag = t.analysis.get("narrative_tag", "")
                fh.write(f"{i}. **{t.symbol}** — {t.chg_h24:+.0f}% · "
                         f"vol ${t.vol_h24:,.0f}"
                         + (f" · _{tag}_" if tag else "") + "\n")
            if pm.get("scorecard"):
                s = pm["scorecard"]
                fh.write(f"\n**Past calls reviewed:** {s['reviewed']} — "
                         f"{s['good']} up, {s['bad'] + s['dead']} down "
                         f"(median {s['median_change']:+.0f}%)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
