"""Wash-trading, bot-volume and rug-risk detection.

Two independent scores, never blended:
  - wash_score  0-100 : probability the volume is manufactured
  - rug_score   0-100 : probability the token is a structural trap

A token can have perfectly real volume AND still be a rug (and vice versa).
Collapsing the two into one number is the classic failure of automated trackers.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------- model


def _f(x: Any, default: float = 0.0) -> float:
    try:
        v = float(x)
        return v if v == v and v not in (float("inf"), float("-inf")) else default
    except (TypeError, ValueError):
        return default


@dataclass
class Token:
    mint: str
    symbol: str
    name: str
    pair_address: str = ""
    dex: str = ""
    url: str = ""
    price_usd: float = 0.0
    mcap: float = 0.0
    fdv: float = 0.0
    liquidity: float = 0.0
    vol_h24: float = 0.0
    vol_h6: float = 0.0
    vol_h1: float = 0.0
    chg_h24: float = 0.0
    chg_h6: float = 0.0
    chg_h1: float = 0.0
    chg_m5: float = 0.0
    buys_h24: int = 0
    sells_h24: int = 0
    buys_h1: int = 0
    sells_h1: int = 0
    created_at: int = 0          # ms epoch
    boosted: int = 0
    websites: list[str] = field(default_factory=list)
    socials: dict[str, str] = field(default_factory=dict)
    description: str = ""
    # derived
    wash_score: float = 0.0
    wash_flags: list[str] = field(default_factory=list)
    rug_score: float = 0.0
    rug_flags: list[str] = field(default_factory=list)
    hard_reject: str = ""
    kol_buyers: int = 0
    kol_weight: float = 0.0
    kol_names: list[str] = field(default_factory=list)
    score: float = 0.0
    score_parts: dict[str, float] = field(default_factory=dict)

    @property
    def age_hours(self) -> float:
        if not self.created_at:
            return 9999.0
        return max((time.time() * 1000 - self.created_at) / 3_600_000, 0.0)

    @property
    def txns_h24(self) -> int:
        return self.buys_h24 + self.sells_h24

    @property
    def vol_liq(self) -> float:
        return self.vol_h24 / self.liquidity if self.liquidity > 0 else 0.0

    @property
    def liq_mcap(self) -> float:
        return self.liquidity / self.mcap if self.mcap > 0 else 0.0

    @property
    def avg_trade(self) -> float:
        return self.vol_h24 / self.txns_h24 if self.txns_h24 else 0.0


def normalize_pair(p: dict) -> Token | None:
    """Turn a Dexscreener pair into a Token. Keeps the deepest pool."""
    base = p.get("baseToken") or {}
    mint = base.get("address")
    if not mint:
        return None
    txns = p.get("txns") or {}
    vol = p.get("volume") or {}
    chg = p.get("priceChange") or {}
    liq = p.get("liquidity") or {}
    info = p.get("info") or {}

    socials = {}
    for s in info.get("socials") or []:
        plat = (s.get("platform") or s.get("type") or "").lower()
        handle = s.get("handle") or s.get("url") or ""
        if plat and handle:
            socials[plat] = handle

    def tx(window: str, side: str) -> int:
        return int(_f((txns.get(window) or {}).get(side)))

    return Token(
        mint=mint,
        symbol=(base.get("symbol") or "?")[:16],
        name=(base.get("name") or "")[:64],
        pair_address=p.get("pairAddress", ""),
        dex=p.get("dexId", ""),
        url=p.get("url", ""),
        price_usd=_f(p.get("priceUsd")),
        mcap=_f(p.get("marketCap")),
        fdv=_f(p.get("fdv")),
        liquidity=_f(liq.get("usd")),
        vol_h24=_f(vol.get("h24")),
        vol_h6=_f(vol.get("h6")),
        vol_h1=_f(vol.get("h1")),
        chg_h24=_f(chg.get("h24")),
        chg_h6=_f(chg.get("h6")),
        chg_h1=_f(chg.get("h1")),
        chg_m5=_f(chg.get("m5")),
        buys_h24=tx("h24", "buys"),
        sells_h24=tx("h24", "sells"),
        buys_h1=tx("h1", "buys"),
        sells_h1=tx("h1", "sells"),
        created_at=int(_f(p.get("pairCreatedAt"))),
        boosted=int(_f((p.get("boosts") or {}).get("active"))),
        websites=[w.get("url", "") for w in (info.get("websites") or []) if w.get("url")],
        socials=socials,
    )


def dedupe_best_pair(pairs: list[dict]) -> dict[str, Token]:
    """One mint can have N pools. Keep the one carrying the liquidity."""
    best: dict[str, Token] = {}
    for p in pairs:
        t = normalize_pair(p)
        if not t:
            continue
        cur = best.get(t.mint)
        if cur is None or t.liquidity > cur.liquidity:
            if cur is not None:
                # volume accumulates across every pool of the token
                t.vol_h24 = max(t.vol_h24, cur.vol_h24)
            best[t.mint] = t
    return best


# ----------------------------------------------------------- wash / bot volume

def score_wash(t: Token, cfg: dict) -> None:
    """Rate how credible the volume is. Additive, capped at 100.

    Each signal is weak on its own — what matters is how many stack up.
    A real runner trips zero or one; an inflated chart trips four or more.
    """
    score = 0.0
    flags: list[str] = []

    # 1. Volume out of proportion with pool depth.
    #    A pool cannot absorb 100x its liquidity in 24h without breaking price.
    vl = t.vol_liq
    if vl > cfg["vol_liq_extreme"]:
        score += 30
        flags.append(f"volume {vl:.0f}× liquidity")
    elif vl > cfg["vol_liq_high"]:
        score += 15
        flags.append(f"volume {vl:.0f}× liquidity")

    # 2. Heavy volume, no price movement: money going in circles.
    if vl > cfg["vol_liq_high"] and abs(t.chg_h24) < cfg["flat_price_pct"]:
        score += 25
        flags.append(f"heavy volume but flat price ({t.chg_h24:+.1f}%)")

    # 3. Buy/sell symmetry too clean — the signature of bot round-trips.
    if t.txns_h24 > 300:
        sym = abs(t.buys_h24 - t.sells_h24) / t.txns_h24
        if sym < cfg["symmetry_tight"]:
            score += 20
            flags.append(f"buys/sells symmetric to {sym * 100:.1f}%")

    # 4. Tiny average trade size: micro-transaction spam.
    if t.avg_trade and t.avg_trade < cfg["micro_trade_usd"] and t.txns_h24 > 500:
        score += 15
        flags.append(f"${t.avg_trade:.0f} average trade")

    # 5. Constant cadence. A real run comes in bursts, not on a metronome.
    if t.txns_h24 > 400 and (t.buys_h1 + t.sells_h1) > 0:
        projected = (t.buys_h1 + t.sells_h1) * 24
        drift = abs(projected - t.txns_h24) / t.txns_h24
        if drift < cfg["cadence_flat"]:
            score += 15
            flags.append("machine-like transaction cadence")

    # 6. Same trade size at 1h and 24h: bot with a hardcoded amount.
    tx_h1 = t.buys_h1 + t.sells_h1
    if tx_h1 > 40 and t.avg_trade > 0:
        avg_h1 = t.vol_h1 / tx_h1
        if abs(avg_h1 - t.avg_trade) / t.avg_trade < cfg["size_uniform"]:
            score += 10
            flags.append("uniform trade size")

    # 7. Inflated market cap on an empty pool: the chart is decorative.
    if t.mcap > 0 and 0 < t.liq_mcap < cfg["liq_mcap_thin"]:
        score += 15
        flags.append(f"liquidity at {t.liq_mcap * 100:.1f}% of mcap")

    t.wash_score = min(score, 100.0)
    t.wash_flags = flags


# ------------------------------------------------------------------- rug risk

_HARD_RISKS = {
    "mint authority still enabled": "mint authority active",
    "freeze authority still enabled": "freeze authority active",
    "freeze authority enabled": "freeze authority active",
    "mint authority enabled": "mint authority active",
}

_SOFT_RISKS = {
    "large amount of lp unlocked": (30, "LP unlocked"),
    "low liquidity": (20, "low liquidity"),
    "single holder ownership": (25, "single dominant holder"),
    "high ownership": (20, "holder concentration"),
    "top 10 holders high ownership": (25, "top 10 holders too concentrated"),
    "copycat token": (20, "copycat token"),
    "low amount of lp providers": (15, "few LP providers"),
}


def score_rug(t: Token, report: dict | None, cfg: dict) -> None:
    """Translate the RugCheck report into a score plus rejection reasons."""
    score = 0.0
    flags: list[str] = []

    if report is None:
        # No report means uncertainty, not innocence.
        t.rug_score = cfg["unknown_rug_score"]
        t.rug_flags = ["no risk report available"]
        return

    for risk in report.get("risks") or []:
        label = (risk.get("name") or "").strip().lower()
        level = (risk.get("level") or "").lower()

        for key, msg in _HARD_RISKS.items():
            if key in label:
                t.hard_reject = msg
                score += 60
                flags.append(msg)
                break
        else:
            for key, (pts, msg) in _SOFT_RISKS.items():
                if key in label:
                    score += pts if level in ("danger", "warn", "warning") else pts * 0.5
                    flags.append(msg)
                    break

    # RugCheck's own score as a secondary input (higher = riskier).
    raw = _f(report.get("score_normalised") or report.get("score"))
    if raw > cfg["rugcheck_score_alarm"]:
        score += 15
        flags.append(f"high RugCheck score ({raw:.0f})")

    t.rug_score = min(score, 100.0)
    t.rug_flags = list(dict.fromkeys(flags))


# ----------------------------------------------------------------------- gates

def passes_gates(t: Token, cfg: dict) -> tuple[bool, str]:
    """Hard filters applied before any ranking.

    The goal is to never publish a token you would regret the next morning.
    Missing a runner costs less than calling one that rugs within the hour.
    """
    if t.hard_reject:
        return False, t.hard_reject
    if t.liquidity < cfg["min_liquidity_usd"]:
        return False, f"liquidity < ${cfg['min_liquidity_usd']:,.0f}"
    if t.vol_h24 < cfg["min_volume_usd"]:
        return False, f"volume < ${cfg['min_volume_usd']:,.0f}"
    if t.mcap and t.mcap < cfg["min_mcap_usd"]:
        return False, "market cap too small"
    if t.age_hours < cfg["min_age_hours"]:
        return False, f"pair younger than {cfg['min_age_hours']}h"
    if t.txns_h24 < cfg["min_txns"]:
        return False, "too few transactions"
    if t.wash_score >= cfg["wash_reject"]:
        return False, f"suspicious volume ({t.wash_score:.0f}/100)"
    if t.rug_score >= cfg["rug_reject"]:
        return False, f"structural risk ({t.rug_score:.0f}/100)"
    if t.chg_h24 < cfg["min_change_pct"]:
        return False, "no meaningful move"
    return True, ""
