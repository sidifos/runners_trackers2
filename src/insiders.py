"""Holder structure and insider concentration.

The summary RugCheck endpoint only returns risk labels. That is not enough:
a token can show "32% insiders" and still clear a label-only check. This module
pulls the full report and extracts the numbers, so concentration becomes a
first-class, quantified gate rather than a footnote.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sources import RC, _get

log = logging.getLogger("insiders")

# Addresses that legitimately hold large balances and must never count as
# concentration: AMM vaults, burn address, known program-owned accounts.
BENIGN_OWNERS = {
    "11111111111111111111111111111111",
    "1nc1nerator11111111111111111111111111111111",
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",  # Raydium authority
    "GThUX1Atko4tqhN2NaiTazWSeFWMuiUvfFnyJyUghFMJ",
}


@dataclass
class HolderProfile:
    top10_pct: float = 0.0
    top1_pct: float = 0.0
    insider_pct: float = 0.0
    holder_count: int = 0
    lp_locked_pct: float = 0.0
    bundled: bool = False
    creator_balance_pct: float = 0.0
    available: bool = False
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "top10_pct": round(self.top10_pct, 1),
            "top1_pct": round(self.top1_pct, 1),
            "insider_pct": round(self.insider_pct, 1),
            "holder_count": self.holder_count,
            "lp_locked_pct": round(self.lp_locked_pct, 1),
            "bundled": self.bundled,
            "creator_balance_pct": round(self.creator_balance_pct, 1),
            "available": self.available,
            "notes": self.notes,
        }


def _pct(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    # RugCheck mixes fractions and percentages depending on the field.
    return v * 100 if 0 < v <= 1 else v


def fetch_profile(mint: str) -> HolderProfile:
    """Full RugCheck report → quantified holder structure."""
    p = HolderProfile()
    data = _get("rc", f"{RC}/tokens/{mint}/report")
    if not isinstance(data, dict):
        p.notes.append("no holder report available")
        return p

    p.available = True
    p.holder_count = int(_pct(data.get("totalHolders")) or 0) if isinstance(
        data.get("totalHolders"), (int, float)
    ) else 0

    holders = data.get("topHolders") or []
    real = []
    for h in holders:
        if not isinstance(h, dict):
            continue
        owner = h.get("owner") or h.get("address") or ""
        if owner in BENIGN_OWNERS or h.get("insider") is None and h.get("pct") is None:
            if owner in BENIGN_OWNERS:
                continue
        pct = _pct(h.get("pct") if h.get("pct") is not None else h.get("uiAmountPercent"))
        if pct <= 0:
            continue
        real.append({"owner": owner, "pct": pct, "insider": bool(h.get("insider"))})

    real.sort(key=lambda x: -x["pct"])
    if real:
        p.top1_pct = real[0]["pct"]
        p.top10_pct = sum(h["pct"] for h in real[:10])
        p.insider_pct = sum(h["pct"] for h in real if h["insider"])

    # Insider networks (bundled launches, funded-from-one-source clusters).
    networks = data.get("insiderNetworks") or []
    if isinstance(networks, list) and networks:
        p.bundled = True
        net_pct = 0.0
        for n in networks:
            if isinstance(n, dict):
                net_pct += _pct(n.get("tokenAmountPct") or n.get("pct"))
        p.insider_pct = max(p.insider_pct, net_pct)
        p.notes.append(f"{len(networks)} insider network(s) detected")

    # LP lock status across markets.
    markets = data.get("markets") or []
    locks = []
    for m in markets:
        if not isinstance(m, dict):
            continue
        lp = m.get("lp") or {}
        val = lp.get("lpLockedPct")
        if val is not None:
            locks.append(_pct(val))
    if locks:
        p.lp_locked_pct = max(locks)

    creator = data.get("creatorBalance") or data.get("creator_balance")
    supply = data.get("totalSupply") or data.get("supply")
    try:
        if creator and supply and float(supply) > 0:
            p.creator_balance_pct = float(creator) / float(supply) * 100
    except (TypeError, ValueError):
        pass

    if p.insider_pct > 0:
        p.notes.append(f"{p.insider_pct:.0f}% held by flagged insiders")
    if p.top10_pct > 0:
        p.notes.append(f"top 10 hold {p.top10_pct:.0f}%")
    return p


def apply_gates(token, profile: HolderProfile, cfg: dict) -> tuple[bool, str]:
    """Concentration gates. Returns (passes, reason).

    These run in addition to the wash and rug gates. The $PENSION case — heavy
    insider ownership with no tracked-wallet confirmation — is cut here.
    """
    if not profile.available:
        return True, ""  # absence handled by the unknown-rug penalty upstream

    if profile.insider_pct >= cfg["insider_reject_pct"]:
        return False, f"{profile.insider_pct:.0f}% insider-held"
    if profile.top10_pct >= cfg["top10_reject_pct"]:
        return False, f"top 10 hold {profile.top10_pct:.0f}%"
    if profile.top1_pct >= cfg["top1_reject_pct"]:
        return False, f"single holder at {profile.top1_pct:.0f}%"
    if (profile.holder_count and
            profile.holder_count < cfg["min_holders"]):
        return False, f"only {profile.holder_count} holders"

    # Elevated-but-not-fatal concentration requires tracked-wallet confirmation.
    # A crowded cap table with nobody credible buying is the classic exit-liquidity
    # setup: the structure alone is not proof of fraud, but it removes the benefit
    # of the doubt.
    if (profile.insider_pct >= cfg["insider_needs_kol_pct"]
            and token.kol_buyers < cfg["insider_kol_required"]):
        return False, (f"{profile.insider_pct:.0f}% insiders and "
                       f"{token.kol_buyers} tracked wallets")
    return True, ""
