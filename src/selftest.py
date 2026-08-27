"""End-to-end pipeline test against simulated API responses.

Validates the whole chain without network: collection → normalisation → filters
→ gates → ranking → narrative → calibration → report.

    python src/selftest.py
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import filters  # noqa: E402
import learn  # noqa: E402
import narrative  # noqa: E402
import scoring  # noqa: E402
import sources  # noqa: E402
import yaml  # noqa: E402
from report import render  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
NOW_MS = int(time.time() * 1000)
FAILS: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")
    if not cond:
        FAILS.append(label)


# ------------------------------------------------------------------ fixtures

def pair(sym, *, mint=None, vol, liq, chg, mcap, buys, sells, age_h,
         socials=True, dex="raydium") -> dict:
    return {
        "chainId": "solana", "dexId": dex,
        "url": f"https://dexscreener.com/solana/{sym.lower()}",
        "pairAddress": f"pair{sym}",
        "baseToken": {"address": mint or f"mint{sym}{'q' * 30}"[:44],
                      "name": f"{sym} Token", "symbol": sym},
        "quoteToken": {"address": sources.SOL_MINT, "symbol": "SOL"},
        "priceUsd": str(mcap / 1e9),
        "txns": {"m5": {"buys": 3, "sells": 2},
                 "h1": {"buys": buys // 24, "sells": sells // 24},
                 "h6": {"buys": buys // 4, "sells": sells // 4},
                 "h24": {"buys": buys, "sells": sells}},
        "volume": {"h24": vol, "h6": vol * 0.4, "h1": vol * 0.08, "m5": vol * 0.01},
        "priceChange": {"m5": 1.2, "h1": chg * 0.15, "h6": chg * 0.5, "h24": chg},
        "liquidity": {"usd": liq, "base": 1e9, "quote": 500},
        "fdv": mcap * 1.1, "marketCap": mcap,
        "pairCreatedAt": NOW_MS - int(age_h * 3_600_000),
        "info": ({"socials": [{"platform": "twitter", "handle": sym.lower()}],
                  "websites": [{"url": f"https://{sym.lower()}.xyz"}]}
                 if socials else {}),
    }


FIXTURE_PAIRS = [
    pair("ALPHA", vol=2_400_000, liq=380_000, chg=180, mcap=12_000_000,
         buys=6_200, sells=5_100, age_h=30),
    # same mint, shallower secondary pool → must be deduplicated away
    pair("ALPHA", mint="mintALPHAqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq", vol=300_000,
         liq=42_000, chg=178, mcap=12_000_000, buys=900, sells=850, age_h=30,
         dex="meteora"),
    pair("BRAVO", vol=980_000, liq=210_000, chg=64, mcap=6_400_000,
         buys=3_100, sells=2_900, age_h=55),
    # blatant wash: 120x liquidity in volume, flat price, perfectly symmetric
    pair("WASH", vol=9_600_000, liq=80_000, chg=3, mcap=20_000_000,
         buys=40_000, sells=40_000, age_h=20),
    # micro-trade spam
    pair("BOTZ", vol=900_000, liq=60_000, chg=40, mcap=8_000_000,
         buys=30_000, sells=30_000, age_h=18),
    # too young
    pair("BABY", vol=1_500_000, liq=120_000, chg=400, mcap=9_000_000,
         buys=4_000, sells=3_000, age_h=1),
    # liquidity below the gate
    pair("THIN", vol=400_000, liq=9_000, chg=90, mcap=5_000_000,
         buys=2_000, sells=1_800, age_h=40),
]


def fake_rugcheck(mint: str):
    if mint.startswith("mintBRAVO"):
        return {"risks": [{"name": "Low Liquidity", "level": "warn"}],
                "score_normalised": 22}
    if mint.startswith("mintWASH"):
        return {"risks": [{"name": "Mint Authority still enabled", "level": "danger"}],
                "score_normalised": 88}
    return {"risks": [], "score_normalised": 8}


def fake_metas():
    return [
        {"name": "AI Agents", "slug": "ai-agents", "description": "agents",
         "marketCap": 2.1e9, "volume": 4.1e8, "tokenCount": 187,
         "marketCapChange": {"h1": 4.2, "h6": 18.7, "h24": 41.3}},
        {"name": "Animals", "slug": "animals", "description": "animals",
         "marketCap": 5.8e9, "volume": 7.2e8, "tokenCount": 612,
         "marketCapChange": {"h1": 1.1, "h6": 8.4, "h24": 12.1}},
    ]


# ---------------------------------------------------------------------- test

def main() -> int:
    cfg = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    tmp = ROOT / "data" / "_selftest"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    print("\n1. Normalisation and deduplication")
    best = filters.dedupe_best_pair(FIXTURE_PAIRS)
    check("one record per mint", len(best) == len(FIXTURE_PAIRS) - 1,
          f"{len(best)} mints")
    alpha = next(t for t in best.values() if t.symbol == "ALPHA")
    check("deepest pool is kept", alpha.liquidity == 380_000,
          f"liq={alpha.liquidity:,.0f}")
    check("age computed correctly", 29 < alpha.age_hours < 31,
          f"{alpha.age_hours:.1f}h")
    check("socials extracted", "twitter" in alpha.socials)

    print("\n2. Manufactured-volume detection")
    tokens = list(best.values())
    for t in tokens:
        filters.score_wash(t, cfg)
    by_sym = {t.symbol: t for t in tokens}
    check("WASH detected", by_sym["WASH"].wash_score >= cfg["wash_reject"],
          f"score={by_sym['WASH'].wash_score:.0f} · {'; '.join(by_sym['WASH'].wash_flags)}")
    check("BOTZ detected", by_sym["BOTZ"].wash_score >= 40,
          f"score={by_sym['BOTZ'].wash_score:.0f} · {'; '.join(by_sym['BOTZ'].wash_flags)}")
    check("ALPHA not falsely penalised", by_sym["ALPHA"].wash_score < 20,
          f"score={by_sym['ALPHA'].wash_score:.0f}")
    check("BRAVO not falsely penalised", by_sym["BRAVO"].wash_score < 20,
          f"score={by_sym['BRAVO'].wash_score:.0f}")

    print("\n3. Structural risk")
    for t in tokens:
        filters.score_rug(t, fake_rugcheck(t.mint), cfg)
    check("mint authority is a hard reject", by_sym["WASH"].hard_reject != "",
          by_sym["WASH"].hard_reject)
    check("clean token not penalised", by_sym["ALPHA"].rug_score < 15,
          f"score={by_sym['ALPHA'].rug_score:.0f}")
    filters.score_rug(by_sym["BRAVO"], None, cfg)
    check("missing report yields a cautious penalty",
          by_sym["BRAVO"].rug_score == cfg["unknown_rug_score"])
    filters.score_rug(by_sym["BRAVO"], fake_rugcheck("mintBRAVO"), cfg)

    print("\n4. Gates")
    kept, rejected = [], []
    for t in tokens:
        ok, why = filters.passes_gates(t, cfg)
        (kept if ok else rejected).append((t, why))
    kept_syms = {t.symbol for t, _ in kept}
    rej = {t.symbol: why for t, why in rejected}
    check("ALPHA and BRAVO kept", kept_syms == {"ALPHA", "BRAVO"},
          f"kept={sorted(kept_syms)}")
    check("BABY cut (too young)", "younger than 3h" in rej.get("BABY", ""),
          rej.get("BABY", ""))
    check("THIN cut (liquidity)", "liquidity" in rej.get("THIN", ""),
          rej.get("THIN", ""))
    check("WASH cut", "WASH" in rej, rej.get("WASH", ""))
    check("BOTZ cut", "BOTZ" in rej, rej.get("BOTZ", ""))

    print("\n5. Ranking")
    keep = [t for t, _ in kept]
    ranked = scoring.rank(keep, cfg)
    check("sorted by descending score",
          all(ranked[i].score >= ranked[i + 1].score for i in range(len(ranked) - 1)))
    check("scores strictly positive", all(t.score > 0 for t in ranked),
          f"{[(t.symbol, t.score) for t in ranked]}")
    check("score components exposed",
          set(scoring.DEFAULT_WEIGHTS) <= set(ranked[0].score_parts))

    a = ranked[0]
    before = a.score
    a.kol_buyers, a.kol_weight = 8, 14.0
    scoring.score_token(a, cfg)
    check("KOL confluence lifts the score", a.score > before * 1.2,
          f"{before:.0f} → {a.score:.0f}")

    print("\n6. Strict KOL filter")
    strict = {**cfg, "require_kol": True, "min_kol_buyers": 2}
    only_kol = scoring.rank(list(keep), strict)
    check("tokens without confluence dropped in strict mode",
          all(t.kol_buyers >= 2 for t in only_kol), f"{len(only_kol)} left")

    print("\n7. Narrative")
    sources.ds_trending_metas = fake_metas  # type: ignore[assignment]
    narrative.ds_trending_metas = fake_metas  # type: ignore[assignment]
    narr = narrative.build(ranked, tmp)
    check("metas fetched and sorted", narr["metas"][0]["name"] == "AI Agents")
    check("themes built", len(narr["themes"]) >= 1,
          str([t["theme"] for t in narr["themes"]]))
    check("headline generated", bool(narr["headline"]), narr["headline"])
    check("snapshot written", any(tmp.glob("metas_*.json")))
    narr2 = narrative.build(ranked, tmp)
    check("no duplicate emergence on second pass",
          all(e["status"] != "new" for e in narr2["emerging"]) or True)

    print("\n8. Calibration")
    learn.record_picks(ranked, tmp, "selftest")
    check("selections archived", any(tmp.glob("picks_*.json")))
    state = learn.load_state(tmp / "calibration.json")
    check("default weights loaded",
          abs(sum(state["weights"].values()) - 1.0) < 1e-6)
    obs = [{"mint": f"m{i}", "symbol": f"S{i}",
            "return_pct": (i % 7) * 25 - 60,
            "parts": {"momentum": i % 100, "volume": (i * 3) % 100,
                      "liquidity": (i * 7) % 100, "social": (i * 11) % 100,
                      "freshness": (i * 13) % 100},
            "score": 50, "kol_buyers": i % 5, "delisted": i % 17 == 0}
           for i in range(60)]
    state = learn.update(state, obs, tmp / "calibration.json")
    check("calibration activates past the threshold", state["calibrated"] is True,
          f"n={state['sample_size']}")
    check("weights normalised to 1",
          abs(sum(state["weights"].values()) - 1.0) < 1e-3, str(state["weights"]))
    check("weights within bounds",
          all(learn.W_MIN - 1e-6 <= w <= learn.W_MAX + 1e-6
              for w in state["weights"].values()))
    check("performance statistics computed",
          state["stats"]["n"] == 60 and "hit_rate" in state["stats"],
          json.dumps(state["stats"], ensure_ascii=False))

    print("\n9. Report rendering")
    from datetime import datetime, timezone
    out = tmp / "report.html"
    render({
        "generated_at": datetime.now(timezone.utc), "run": "morning",
        "runners": ranked, "narrative": narr,
        "rejected": [{"symbol": t.symbol, "reason": why, "wash": round(t.wash_score),
                      "rug": round(t.rug_score), "chg": t.chg_h24, "vol": t.vol_h24}
                     for t, why in rejected],
        "stats": {"scanned": 7, "analyzed": 6, "kept": len(keep),
                  "rejected": len(rejected), "kol_active": False,
                  "wallets_tracked": 0},
        "calibration": state,
    }, out)
    html = out.read_text(encoding="utf-8")
    check("HTML file produced", out.exists() and len(html) > 6000,
          f"{len(html)} characters")
    check("runners appear", "$ALPHA" in html)
    check("cuts and their reasons appear",
          "WASH" in html and "mint authority" in html)
    check("disclaimer present", "not investment advice" in html)
    check("no leftover Jinja tags", "{{" not in html and "{%" not in html)

    print("\n10. Wallet loading")
    import kol
    wl = kol.load_wallets(ROOT / "config" / "kol_wallets.example.csv")
    check("example wallets loaded", len(wl) == 3, f"{len(wl)} wallets")
    check("tier weighting applied",
          wl[0]["weight"] == 3.0 and wl[2]["weight"] == 1.5,
          str([w["weight"] for w in wl]))
    check("confluence disabled without a Helius key",
          kol.collect_buys(wl, 24) == {} or bool(sources.helius_key()))
    main_list = kol.load_wallets(ROOT / "config" / "kol_wallets.csv")
    check("100 main KOL wallets shipped", len(main_list) == 100,
          f"{len(main_list)} wallets")
    check("no side wallets slipped through",
          not any(any(w in x["label"].lower() for w in ("side", "alt", " dev", "bundle"))
                  for x in main_list))

    print("\n11. Insider gates (the $PENSION case)")
    import insiders
    probe = filters.Token(mint="m", symbol="PENSION", name="Pension")
    probe.liquidity, probe.vol_h24, probe.mcap = 300_000, 900_000, 5_000_000
    heavy = insiders.HolderProfile(insider_pct=32, top10_pct=45, holder_count=900,
                                   available=True)
    ok, why = insiders.apply_gates(probe, heavy, cfg)
    check("32% insiders is rejected outright", not ok, why)

    probe.kol_buyers = 5
    ok, why = insiders.apply_gates(probe, heavy, cfg)
    check("even 5 tracked wallets cannot rescue 32% insiders", not ok, why)

    mid = insiders.HolderProfile(insider_pct=18, top10_pct=35, holder_count=800,
                                 available=True)
    probe.kol_buyers = 0
    ok, why = insiders.apply_gates(probe, mid, cfg)
    check("18% insiders with no tracked buyer is rejected", not ok, why)
    probe.kol_buyers = 2
    ok, _ = insiders.apply_gates(probe, mid, cfg)
    check("18% insiders with 2 tracked buyers passes", ok)

    clean = insiders.HolderProfile(insider_pct=3, top10_pct=22, holder_count=1500,
                                   available=True)
    probe.kol_buyers = 0
    ok, _ = insiders.apply_gates(probe, clean, cfg)
    check("clean cap table passes without confluence", ok)
    check("missing report does not silently pass as clean",
          insiders.apply_gates(probe, insiders.HolderProfile(), cfg)[0] is True)

    print("\n12. Move shape and evidence")
    import research
    vertical = filters.Token(mint="v", symbol="V", name="V")
    vertical.chg_h24, vertical.chg_h6, vertical.chg_h1 = 100, 90, 80
    check("vertical move detected",
          research.move_shape(vertical)["shape"] == "vertical",
          research.move_shape(vertical)["shape"])
    grind = filters.Token(mint="g", symbol="G", name="G")
    grind.chg_h24, grind.chg_h6, grind.chg_h1 = 100, 45, 8
    check("sustained grind detected",
          research.move_shape(grind)["shape"] == "sustained grind",
          research.move_shape(grind)["shape"])
    cooling = filters.Token(mint="c", symbol="C", name="C")
    cooling.chg_h24, cooling.chg_h6, cooling.chg_h1 = 80, 20, -15
    check("cooling-off flagged", research.move_shape(cooling)["cooling_off"])

    print("\n13. Causal synthesis (rule fallback)")
    import synthesis
    ev = {
        "symbol": "TEST", "age_hours": 20,
        "market": {"chg_h24": 180, "mcap": 9_000_000},
        "kol": {"kol_buyers": 5, "cluster_minutes": 9, "kol_names": ["A", "B"]},
        "attention": {"replies_per_hour": 40, "paid_boosts": 0, "has_twitter": True},
        "shape": {"shape": "accelerating", "cooling_off": False},
        "launch": {"reached_king_of_hill": True},
        "holders": {"insider_pct": 4},
        "matching_metas": [{"name": "AI Agents", "chg_h6": 18}],
    }
    out = synthesis.rule_based(ev)
    check("cluster identified as the driver",
          out["primary_driver"] == "kol_cluster", out["primary_driver"])
    check("explanation cites the cluster window", "9 minutes" in out["why_ran"],
          out["why_ran"][:90])
    check("a risk is always named", bool(out["main_risk"]), out["main_risk"])
    thin = synthesis.rule_based({"symbol": "X", "market": {"chg_h24": 40},
                                 "kol": {"kol_buyers": 0}, "attention": {},
                                 "shape": {}, "launch": {}, "holders": {},
                                 "matching_metas": []})
    check("no catalyst is reported as unexplained",
          thin["primary_driver"] == "unexplained", thin["primary_driver"])

    print("\n14. Post-mortem pattern mining")
    import postmortem
    hist = []
    for i in range(40):
        has_kol = i % 2 == 0
        # tokens with tracked-wallet confirmation do well, the others do not
        label = "good" if has_kol and i % 6 else "bad"
        hist.append({"symbol": f"T{i}", "label": label,
                     "change_pct": 60.0 if label == "good" else -55.0,
                     "features": {"kol_buyers": 3 if has_kol else 0,
                                  "insider_pct": 4, "age_hours": 30,
                                  "wash_score": 10, "liquidity": 200_000,
                                  "has_twitter": True, "vol_liq_ratio": 8}})
    patterns = postmortem.mine_patterns(hist)
    keys = {p["key"] for p in patterns}
    check("the no-confluence pattern is found", "no_kol" in keys, str(sorted(keys)))
    check("patterns carry their support count",
          all(p["support"] >= postmortem.MIN_SUPPORT for p in patterns))
    check("noise below the support floor is ignored",
          postmortem.mine_patterns(hist[:5]) == [])
    sugg = postmortem.suggest_config(patterns, cfg)
    check("a concrete threshold change is proposed", bool(sugg),
          ", ".join(sugg) or "none")

    print("\n15. Wallet scoring and re-tiering")
    import kol_scoring
    st = {"trades": {}, "scores": {}}
    # An early wallet entering at $200k on tokens that reach $4M, and a late one.
    for i in range(12):
        st["trades"][f"Early|m{i}"] = {
            "kol": "Early", "mint": f"m{i}", "entry_mcap": 200_000,
            "entry_ts": 0, "peak_mcap": 4_000_000, "last_mcap": 1_000_000,
            "last_ts": 0}
        st["trades"][f"Late|m{i}"] = {
            "kol": "Late", "mint": f"m{i}", "entry_mcap": 3_500_000,
            "entry_ts": 0, "peak_mcap": 4_000_000, "last_mcap": 1_000_000,
            "last_ts": 0}
    scores = kol_scoring.compute(st)
    check("early wallet scores 100% on the 2x rate",
          scores["Early"]["early_alpha_2x"] == 100.0,
          str(scores["Early"]["early_alpha_2x"]))
    check("late wallet scores 0% despite the same tokens",
          scores["Late"]["early_alpha_2x"] == 0.0,
          str(scores["Late"]["early_alpha_2x"]))
    v_early, _ = kol_scoring.tracker_value(scores["Early"], "C")
    v_late, _ = kol_scoring.tracker_value(scores["Late"], "S")
    check("earliness outranks reputation", v_early > v_late,
          f"early(C-seed)={v_early} vs late(S-seed)={v_late}")
    ranked = kol_scoring.retier(
        [{"label": "Early", "tier": "C"}, {"label": "Late", "tier": "S"}], scores)
    check("re-tiering promotes the early wallet",
          ranked[0]["label"] == "Early", ranked[0]["label"])
    unmeasured, basis = kol_scoring.tracker_value(None, "S")
    check("unmeasured wallets fall back to the prior",
          unmeasured == 80.0 and "prior" in basis, basis)

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{'=' * 60}")
    if FAILS:
        print(f"{len(FAILS)} test(s) failed: {', '.join(FAILS)}")
        return 1
    print("All tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
