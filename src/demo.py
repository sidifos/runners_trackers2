"""Generate a demo report from realistic synthetic data.

Two purposes: validate the rendering without spending API calls, and show what
the output looks like before the first real run.

    python src/demo.py
"""
from __future__ import annotations

import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import filters  # noqa: E402
import scoring  # noqa: E402
from filters import Token  # noqa: E402
from report import render  # noqa: E402

random.seed(11)
NOW_MS = int(time.time() * 1000)


def mk(symbol, name, *, chg, vol, liq, mcap, age_h, txns, buys_ratio=0.52,
       socials=None, sites=None, kol=0, kol_names=(), desc="") -> Token:
    buys = int(txns * buys_ratio)
    return Token(
        mint=f"{symbol}{'x' * 32}"[:44], symbol=symbol, name=name,
        url=f"https://dexscreener.com/solana/{symbol.lower()}",
        dex="raydium", price_usd=mcap / 1e9,
        mcap=mcap, fdv=mcap * 1.05, liquidity=liq,
        vol_h24=vol, vol_h6=vol * 0.42, vol_h1=vol * 0.09,
        chg_h24=chg, chg_h6=chg * 0.55, chg_h1=chg * 0.14, chg_m5=chg * 0.02,
        buys_h24=buys, sells_h24=txns - buys,
        buys_h1=int(buys * 0.09), sells_h1=int((txns - buys) * 0.09),
        created_at=NOW_MS - int(age_h * 3_600_000),
        socials=socials or {}, websites=list(sites or []),
        description=desc, kol_buyers=kol, kol_weight=kol * 1.6,
        kol_names=list(kol_names),
    )


KEPT = [
    mk("NEURA", "Neura Protocol", chg=312, vol=8_400_000, liq=740_000,
       mcap=41_000_000, age_h=38, txns=24_800,
       socials={"twitter": "neuraprotocol", "telegram": "neura"},
       sites=["https://neura.ai", "https://github.com/neura-labs"],
       kol=9, kol_names=["Cented", "Euris", "Groovy", "Kadenox"],
       desc="autonomous ai agent framework on solana"),
    mk("HIPPO", "Moo Deng Returns", chg=186, vol=5_100_000, liq=430_000,
       mcap=18_600_000, age_h=19, txns=17_200,
       socials={"twitter": "hipporeturns", "telegram": "hippo"},
       kol=7, kol_names=["Mitch", "Assasin", "Waddles"],
       desc="the hippo is back"),
    mk("GROK4", "Grok Terminal", chg=143, vol=3_900_000, liq=512_000,
       mcap=22_400_000, age_h=61, txns=13_400,
       socials={"twitter": "grokterminal"}, sites=["https://grokterminal.xyz"],
       kol=6, kol_names=["Frank", "Loopierr", "Jijo"],
       desc="llm powered trading terminal agent"),
    mk("CAPY", "Capybara Season", chg=97, vol=2_250_000, liq=318_000,
       mcap=9_800_000, age_h=27, txns=9_600,
       socials={"twitter": "capyseason", "telegram": "capy"},
       kol=5, kol_names=["Cooker", "Dior", "Trey"], desc="capybara meme coin"),
    mk("DESCI", "Open Research DAO", chg=88, vol=1_870_000, liq=605_000,
       mcap=14_200_000, age_h=94, txns=7_100,
       socials={"twitter": "openresearchdao"},
       sites=["https://openresearch.org", "https://github.com/ord-dao"],
       kol=4, kol_names=["Tim", "Kev"], desc="desci research funding protocol"),
    mk("PENGU2", "Penguin Winter", chg=71, vol=1_420_000, liq=286_000,
       mcap=7_400_000, age_h=15, txns=8_800,
       socials={"twitter": "penguinwinter"}, kol=4, kol_names=["Ansem", "Zrool"],
       desc="penguin animal meme"),
    mk("AGENTX", "AgentX Swarm", chg=64, vol=1_180_000, liq=402_000,
       mcap=11_900_000, age_h=52, txns=5_900,
       socials={"twitter": "agentxswarm"}, sites=["https://github.com/agentx"],
       kol=3, kol_names=["Meech"], desc="multi agent swarm ai infrastructure"),
    mk("GOLDEN", "Golden Standard", chg=52, vol=940_000, liq=228_000,
       mcap=5_600_000, age_h=41, txns=4_400,
       socials={"twitter": "goldenstd"}, kol=2, kol_names=["Pow"],
       desc="gold backed narrative fed rate meme"),
    mk("WOJAK9", "Wojak Nine", chg=44, vol=760_000, liq=195_000,
       mcap=4_100_000, age_h=22, txns=5_200,
       socials={"twitter": "wojaknine"}, kol=2, kol_names=["Slingoor"],
       desc="wojak meme viral culture"),
    mk("QUEST", "Questline", chg=38, vol=610_000, liq=174_000,
       mcap=3_700_000, age_h=68, txns=3_100,
       socials={"twitter": "questlinegame"}, kol=1, kol_names=["Nate"],
       desc="onchain game quest arena"),
]

REJECTED = [
    (mk("MOONX", "MoonX Infinity", chg=940, vol=14_200_000, liq=41_000,
        mcap=88_000_000, age_h=5, txns=61_000, buys_ratio=0.501),
     "suspicious volume (85/100)"),
    (mk("SAFEG", "SafeGains", chg=420, vol=2_800_000, liq=19_000,
        mcap=31_000_000, age_h=8, txns=9_400), "mint authority active"),
    (mk("PUMPZ", "Pumpz Finance", chg=6, vol=6_700_000, liq=88_000,
        mcap=12_000_000, age_h=13, txns=44_000, buys_ratio=0.500),
     "heavy volume but flat price"),
    (mk("RUGME", "Diamond Hands Only", chg=310, vol=1_900_000, liq=12_000,
        mcap=24_000_000, age_h=2, txns=6_100), "pair younger than 3h"),
    (mk("BOTZ", "BotSwarm", chg=61, vol=3_100_000, liq=27_000,
        mcap=9_400_000, age_h=16, txns=128_000), "$24 average trade"),
    (mk("WHALE1", "Whale Alert Coin", chg=155, vol=880_000, liq=61_000,
        mcap=41_000_000, age_h=31, txns=3_900), "top 10 holders too concentrated"),
]

CFG = {
    "vol_liq_high": 25, "vol_liq_extreme": 70, "flat_price_pct": 15,
    "symmetry_tight": 0.02, "micro_trade_usd": 25, "cadence_flat": 0.10,
    "size_uniform": 0.05, "liq_mcap_thin": 0.02,
    "kol_saturation": 12, "kol_max_multiplier": 1.8,
    "require_kol": False, "min_kol_buyers": 2,
}

NARRATIVE = {
    "headline": "AI agents take the lead — 3 of the top 5 runners, "
                "$13.5M combined volume, median +143%",
    "themes": [
        {"theme": "ai-agents", "count": 3, "tokens": ["NEURA", "GROK4", "AGENTX"],
         "total_volume": 13_480_000, "median_change": 143.0, "avg_score": 71.2},
        {"theme": "animals", "count": 2, "tokens": ["HIPPO", "CAPY", "PENGU2"],
         "total_volume": 8_770_000, "median_change": 97.0, "avg_score": 58.4},
        {"theme": "desci-rwa", "count": 1, "tokens": ["DESCI"],
         "total_volume": 1_870_000, "median_change": 88.0, "avg_score": 54.1},
        {"theme": "internet-culture", "count": 1, "tokens": ["WOJAK9"],
         "total_volume": 760_000, "median_change": 44.0, "avg_score": 41.7},
        {"theme": "tradfi", "count": 1, "tokens": ["GOLDEN"],
         "total_volume": 940_000, "median_change": 52.0, "avg_score": 46.3},
        {"theme": "gaming", "count": 1, "tokens": ["QUEST"],
         "total_volume": 610_000, "median_change": 38.0, "avg_score": 38.9},
    ],
    "keywords": [{"word": "agent", "n": 3}, {"word": "ai", "n": 3}],
    "metas": [
        {"name": "AI Agents", "slug": "ai-agents", "description":
         "Autonomous agents and onchain LLM frameworks",
         "market_cap": 2_140_000_000, "volume": 410_000_000, "token_count": 187,
         "chg_h1": 4.2, "chg_h6": 18.7, "chg_h24": 41.3},
        {"name": "DeSci", "slug": "desci", "description":
         "Decentralised science, research funding",
         "market_cap": 310_000_000, "volume": 44_000_000, "token_count": 41,
         "chg_h1": 2.1, "chg_h6": 14.2, "chg_h24": 22.8},
        {"name": "Animal Memes", "slug": "animals", "description":
         "The historical backbone of the sector",
         "market_cap": 5_800_000_000, "volume": 720_000_000, "token_count": 612,
         "chg_h1": 1.1, "chg_h6": 8.4, "chg_h24": 12.1},
        {"name": "Politics", "slug": "politics", "description":
         "Elections and political figures",
         "market_cap": 890_000_000, "volume": 96_000_000, "token_count": 128,
         "chg_h1": -0.8, "chg_h6": -3.2, "chg_h24": -9.4},
        {"name": "Gaming", "slug": "gaming", "description":
         "Onchain games and guilds",
         "market_cap": 640_000_000, "volume": 51_000_000, "token_count": 94,
         "chg_h1": 0.4, "chg_h6": 2.9, "chg_h24": 5.7},
    ],
    "emerging": [
        {"name": "Agent Swarms", "slug": "agent-swarms", "description":
         "Multi-agent coordination, an AI-agent sub-branch that appeared this week",
         "market_cap": 84_000_000, "volume": 19_000_000, "token_count": 23,
         "chg_h1": 7.9, "chg_h6": 34.1, "chg_h24": 78.2,
         "status": "new", "growth": None},
        {"name": "DeSci", "slug": "desci", "description":
         "Decentralised science, research funding",
         "market_cap": 310_000_000, "volume": 44_000_000, "token_count": 41,
         "chg_h1": 2.1, "chg_h6": 14.2, "chg_h24": 22.8,
         "status": "accelerating", "growth": 62.4},
    ],
}


ANALYSES = {
    "NEURA": {
        "why_ran": "Nine tracked wallets bought inside a 14-minute window last "
                   "night, six of them Tier-S. The GitHub repo went public four "
                   "hours before that cluster, which is what the S-tier wallets "
                   "appear to have been front-running rather than the token itself.",
        "narrative_tag": "shipped code before the buy", "primary_driver": "kol_cluster",
        "confidence": 82, "main_risk": "The repo is a wrapper around an existing "
        "framework; if that gets noticed the thesis collapses fast."},
    "HIPPO": {
        "why_ran": "A revival of a dormant 2024 ticker, re-launched 19h ago and "
                   "reaching king-of-the-hill in 38 minutes. Comment volume is "
                   "running at 61/hour, well above the rest of today's board — "
                   "attention here is real rather than bought.",
        "narrative_tag": "dormant ticker revival", "primary_driver": "organic_attention",
        "confidence": 71, "main_risk": "Revivals decay quickly once the original "
        "holders finish exiting into the new liquidity."},
    "GROK4": {
        "why_ran": "Moved on sector rotation rather than anything token-specific: "
                   "the AI Agents meta is up 19% on 6h and this is the most liquid "
                   "small cap inside it. Three tracked wallets followed the meta in "
                   "rather than leading it.",
        "narrative_tag": "highest-beta AI agent name", "primary_driver": "sector_rotation",
        "confidence": 64, "main_risk": "Pure beta — it gives the move back just as "
        "fast if the meta rolls over."},
    "CAPY": {
        "why_ran": "Five tracked wallets over four hours, no cluster, no catalyst "
                   "in the launch data. The move tracks the animal meta almost "
                   "exactly, which suggests it is being carried rather than led.",
        "narrative_tag": "carried by the animal meta", "primary_driver": "sector_rotation",
        "confidence": 48, "main_risk": "Nothing distinguishes it from a dozen "
        "similar tickers; first to lose attention."},
    "DESCI": {
        "why_ran": "The only runner today with a working product link and a repo "
                   "with commits this week. Grind pattern, not a spike: 88% over "
                   "24h with the last hour still positive.",
        "narrative_tag": "funded research, real repo", "primary_driver": "organic_attention",
        "confidence": 69, "main_risk": "DeSci has repeatedly failed to hold bids "
        "beyond a week in this cycle."},
    "GOLDEN": {
        "why_ran": "Two paid Dexscreener boosts went live six hours before the "
                   "move, and the buy pressure has been flat since. This looks "
                   "bought rather than found.",
        "narrative_tag": "paid visibility", "primary_driver": "paid_promotion",
        "confidence": 58, "main_risk": "Boost-driven moves reverse when the "
        "promotion budget runs out, usually within 48h."},
}

VERDICTS = [
    {"symbol": "PENSION", "entry_mcap": 4_800_000, "current_mcap": 310_000,
     "change_pct": -93.5, "label": "dead", "hours_elapsed": 22.4,
     "diagnosis": "Warning signs that were present: 32% insider supply was "
                  "already flagged; no tracked wallet ever confirmed it. This is "
                  "exactly the combination the insider gate now rejects."},
    {"symbol": "ZAPPY", "entry_mcap": 12_400_000, "current_mcap": 3_100_000,
     "change_pct": -75.0, "label": "bad", "hours_elapsed": 21.1,
     "diagnosis": "Warning signs that were present: volume credibility was already "
                  "marginal at 41/100; the move rested on paid promotion rather "
                  "than real demand."},
    {"symbol": "FROGZ", "entry_mcap": 6_100_000, "current_mcap": 2_900_000,
     "change_pct": -52.5, "label": "bad", "hours_elapsed": 23.0,
     "diagnosis": "No warning present in the recorded evidence — clean cap table, "
                  "four tracked buyers, real socials. Ordinary market risk rather "
                  "than a missed signal."},
    {"symbol": "MOONDOG", "entry_mcap": 2_200_000, "current_mcap": 1_900_000,
     "change_pct": -13.6, "label": "flat", "hours_elapsed": 20.5, "diagnosis": ""},
    {"symbol": "SIGMA", "entry_mcap": 3_400_000, "current_mcap": 4_100_000,
     "change_pct": 20.6, "label": "good", "hours_elapsed": 22.0, "diagnosis": ""},
    {"symbol": "VECTOR", "entry_mcap": 1_800_000, "current_mcap": 5_900_000,
     "change_pct": 227.8, "label": "good", "hours_elapsed": 21.7, "diagnosis": ""},
]

POSTMORTEM = {
    "lessons": [
        "tokens with 15%+ insider supply fail 71% of the time vs 44% baseline "
        "(worse), median outcome -58% over 21 calls",
        "tokens 3+ tracked wallets bought fail 26% of the time vs 44% baseline "
        "(better), median outcome +34% over 38 calls",
        "tokens running paid Dexscreener boosts fail 63% of the time vs 44% "
        "baseline (worse), median outcome -41% over 19 calls",
        "tokens called inside their first 12 hours fail 59% of the time vs 44% "
        "baseline (worse), median outcome -37% over 27 calls",
    ],
    "suggested_config": {
        "insider_reject_pct": {"from": 30, "to": 25,
                               "why": "insider pattern holds over 21 calls"},
        "min_age_hours": {"from": 3, "to": 6,
                          "why": "early calls fail more often"},
    },
    "scorecard": {"reviewed": 118, "good": 42, "flat": 29, "bad": 38, "dead": 9,
                  "median_change": -8.2, "accuracy": 35.6},
    "verdicts": VERDICTS,
}

WALLET_RANKING = [
    {"label": "Cented", "tier": "S", "measured_tier": "S", "tier_changed": False,
     "value": 88.4, "basis": "31 measured entries",
     "metrics": {"calls": 31, "early_alpha_2x": 61.3, "early_alpha_5x": 22.6,
                 "median_entry_mcap": 340_000}},
    {"label": "Absol", "tier": "S", "measured_tier": "S", "tier_changed": False,
     "value": 84.1, "basis": "27 measured entries",
     "metrics": {"calls": 27, "early_alpha_2x": 55.6, "early_alpha_5x": 18.5,
                 "median_entry_mcap": 410_000}},
    {"label": "Kreo", "tier": "A", "measured_tier": "S", "tier_changed": True,
     "value": 79.8, "basis": "24 measured entries",
     "metrics": {"calls": 24, "early_alpha_2x": 58.3, "early_alpha_5x": 16.7,
                 "median_entry_mcap": 290_000}},
    {"label": "Euris", "tier": "S", "measured_tier": "A", "tier_changed": True,
     "value": 61.2, "basis": "19 measured entries",
     "metrics": {"calls": 19, "early_alpha_2x": 36.8, "early_alpha_5x": 5.3,
                 "median_entry_mcap": 2_100_000}},
    {"label": "Trey", "tier": "A", "measured_tier": "A", "tier_changed": False,
     "value": 58.9, "basis": "16 measured entries",
     "metrics": {"calls": 16, "early_alpha_2x": 43.8, "early_alpha_5x": 12.5,
                 "median_entry_mcap": 780_000}},
    {"label": "Ansem", "tier": "S", "measured_tier": "B", "tier_changed": True,
     "value": 44.6, "basis": "11 entries, prior still dominant",
     "metrics": {"calls": 11, "early_alpha_2x": 18.2, "early_alpha_5x": 0.0,
                 "median_entry_mcap": 8_400_000}},
    {"label": "Domy", "tier": "B", "measured_tier": "B", "tier_changed": False,
     "value": 45.0, "basis": "reputation prior", "metrics": None},
]


def main() -> None:
    for t in KEPT:
        filters.score_wash(t, CFG)
        t.rug_score = random.uniform(4, 26)
        t.rug_flags = []
        t.analysis = ANALYSES.get(t.symbol, {})
        t.holders = {
            "insider_pct": round(random.uniform(1, 11), 1),
            "top10_pct": round(random.uniform(18, 34), 1),
            "top1_pct": round(random.uniform(3, 9), 1),
            "holder_count": random.randint(600, 4200),
            "lp_locked_pct": 100.0, "bundled": False,
            "creator_balance_pct": 0.0, "available": True, "notes": [],
        }
    ranked = scoring.rank(KEPT, CFG)

    narrative = dict(NARRATIVE)
    narrative["daily"] = {
        "summary": "Today's board was built on tracked-wallet clusters, not on a "
                   "theme: six of ten runners were confirmed by three or more "
                   "wallets before they moved, and the two that were not are the "
                   "two carrying paid boosts. AI agents look like the label rather "
                   "than the cause — the same wallets bought DESCI and HIPPO with "
                   "the same timing pattern.",
        "rotation": "Money is leaving politics and animal beta and concentrating "
                    "into anything with a working repo.",
        "watch_next": "Agent Swarms is 23 tokens and $84M — the size AI Agents was "
                      "eleven days ago.",
        "source": "llm",
    }

    payload = {
        "generated_at": datetime.now(timezone.utc),
        "run": "morning",
        "runners": ranked,
        "narrative": narrative,
        "postmortem": POSTMORTEM,
        "wallet_ranking": WALLET_RANKING,
        "rejected": [
            {"symbol": t.symbol, "reason": why, "wash": random.randint(58, 92),
             "rug": random.randint(30, 88), "insiders": random.choice([0, 0, 18, 27, 34]),
             "chg": t.chg_h24, "vol": t.vol_h24}
            for t, why in REJECTED
        ],
        "stats": {"scanned": 214, "analyzed": 96, "kept": len(ranked),
                  "rejected": 84, "kol_active": True, "wallets_tracked": 100,
                  "llm_active": True, "researched": 12},
        "calibration": {
            "calibrated": True, "sample_size": 148,
            "weights": {"momentum": 0.2841, "volume": 0.2216, "liquidity": 0.1547,
                        "social": 0.1109, "freshness": 0.2287},
            "correlations": {"momentum": 0.14, "volume": -0.06, "liquidity": 0.21,
                             "social": 0.09, "freshness": 0.18},
            "stats": {"n": 148, "median_return": -12.4, "hit_rate": 38.5,
                      "big_win_rate": 12.8, "rug_rate": 6.1,
                      "kol_median": 34.2, "kol_n": 47},
        },
    }
    out = Path(__file__).resolve().parent.parent / "out" / "demo.html"
    render(payload, out)
    print(f"→ {out}")


if __name__ == "__main__":
    main()
