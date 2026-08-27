"""Holder structure and insider concentration.

The summary RugCheck endpoint only returns risk labels. That is not enough:
a token can show "32% insiders" and still clear a label-only check. This module
pulls the full report and extracts the numbers, so concentration becomes a
first-class, quantified gate rather than a footnote.

Two rules govern the parsing here, both learned the hard way:

* **A share is never rescaled on its own.** Most top-10 holders of a token with
  a real holder base sit below 1% of supply. A "value under 1 must be a
  fraction" rule therefore reads 0.8% as 80%, and a top 10 ends up holding
  538% of the supply. The scale is decided once, for the whole list.

* **An impossible reading is a broken reading, not a verdict.** Shares of one
  supply cannot sum past 100. When they do, the profile is marked unreliable
  and the concentration gates stand down. A parse error that presents itself as
  "every token today is a scam" cuts an entire run and is indistinguishable,
  from the outside, from a quiet market.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sources import RC, _get

log = logging.getLogger("insiders")

# Addresses that legitimately hold large balances and must never count as
# concentration: burn address, system program, known program-owned accounts.
BENIGN_OWNERS = {
    "11111111111111111111111111111111",
    "1nc1nerator11111111111111111111111111111111",
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",  # Raydium authority
    "GThUX1Atko4tqhN2NaiTazWSeFWMuiUvfFnyJyUghFMJ",
}

# knownAccounts types that hold supply for structural reasons rather than as a
# position someone can dump on the market.
BENIGN_ACCOUNT_TYPES = {"amm", "lp", "burn", "vault", "market", "pool"}


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
    reliable: bool = True
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
            "reliable": self.reliable,
            "notes": self.notes,
        }


def _num(x) -> float:
    """A plain float. Nothing is rescaled at this level, on purpose."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _int(x) -> int:
    try:
        return max(int(float(x)), 0)
    except (TypeError, ValueError):
        return 0


def _rescale_shares(values: list[float]) -> list[float]:
    """Put a list of supply shares on a 0-100 scale.

    RugCheck expresses some shares as percentages (4.2 → 4.2%) and some as
    fractions (0.042 → 4.2%), and the field name does not tell you which. The
    decision has to be made for the list as a whole: a list of fractions is
    bounded by 1 in total and by 1 in its largest element, a list of real
    percentages for a token with any pool at all is not.

    Deciding per value is what produced the 538% top 10 — and the run where
    nothing at all cleared the filters.
    """
    positives = [v for v in values if v > 0]
    if len(positives) < 4:
        return values          # too short to read a scale from; trust the unit
    if max(positives) <= 1.0 and sum(positives) <= 1.001:
        return [v * 100 for v in values]
    return values


def _structural_accounts(data: dict) -> set[str]:
    """Addresses holding supply for structural reasons, named by the report.

    On Solana the largest holder of a young token is almost always the AMM pool
    vault. Counting it as concentration makes every honest launch look like a
    whale trap, so the pools the report itself names are excluded by address —
    rather than relying only on a hard-coded list, which can never cover more
    than the venues we happened to think of.
    """
    out = set(BENIGN_OWNERS)

    known = data.get("knownAccounts")
    if isinstance(known, dict):
        for addr, info in known.items():
            kind = (info or {}).get("type") if isinstance(info, dict) else None
            if isinstance(addr, str) and str(kind or "").lower() in BENIGN_ACCOUNT_TYPES:
                out.add(addr)

    for m in data.get("markets") or []:
        if not isinstance(m, dict):
            continue
        for key in ("liquidityA", "liquidityB", "pubkey"):
            v = m.get(key)
            if isinstance(v, str) and v:
                out.add(v)
        lp = m.get("lp")
        if isinstance(lp, dict) and isinstance(lp.get("lpMint"), str):
            out.add(lp["lpMint"])
    return out


def parse_report(data: dict) -> HolderProfile:
    """RugCheck report payload → quantified holder structure.

    Split out from the fetch so the parsing can be tested against fixed
    payloads. It is the parsing, not the gating, that got this wrong.
    """
    p = HolderProfile()
    if not isinstance(data, dict):
        p.notes.append("no holder report available")
        return p

    p.available = True
    p.holder_count = _int(
        data.get("totalHolders")
        if data.get("totalHolders") is not None
        else data.get("holders")
    )

    holders = [h for h in (data.get("topHolders") or []) if isinstance(h, dict)]
    # Scale is read from the untouched list: the pool vault is still in it here,
    # and its presence is what makes a percentage-encoded list unmistakable.
    raw = [_num(h.get("pct") if h.get("pct") is not None else h.get("uiAmountPercent"))
           for h in holders]
    shares = _rescale_shares(raw)

    benign = _structural_accounts(data)
    real = []
    for h, pct in zip(holders, shares):
        owner = h.get("owner") or ""
        addr = h.get("address") or ""
        if owner in benign or addr in benign:
            continue
        if pct <= 0:
            continue
        real.append({"owner": owner or addr, "pct": pct,
                     "insider": bool(h.get("insider"))})

    real.sort(key=lambda x: -x["pct"])
    if real:
        p.top1_pct = real[0]["pct"]
        p.top10_pct = sum(h["pct"] for h in real[:10])
        p.insider_pct = sum(h["pct"] for h in real if h["insider"])

    # Insider networks (bundled launches, funded-from-one-source clusters).
    networks = [n for n in (data.get("insiderNetworks") or []) if isinstance(n, dict)]
    if networks:
        p.bundled = True
        net_raw = [_num(n.get("tokenAmountPct") if n.get("tokenAmountPct") is not None
                        else n.get("pct")) for n in networks]
        net_pct = sum(v for v in _rescale_shares(net_raw) if v > 0)
        p.insider_pct = max(p.insider_pct, net_pct)
        p.notes.append(f"{len(networks)} insider network(s) detected")

    # LP lock status across markets. Read as a percentage; if a venue ever
    # reports a fraction we under-state the lock, which errs towards suspicion.
    locks = []
    for m in data.get("markets") or []:
        if not isinstance(m, dict):
            continue
        lp = m.get("lp") or {}
        if isinstance(lp, dict) and lp.get("lpLockedPct") is not None:
            locks.append(_num(lp["lpLockedPct"]))
    if locks:
        p.lp_locked_pct = min(max(locks), 100.0)

    creator = data.get("creatorBalance") or data.get("creator_balance")
    supply = data.get("totalSupply") or data.get("supply")
    if creator and supply and _num(supply) > 0:
        p.creator_balance_pct = min(_num(creator) / _num(supply) * 100, 100.0)

    _validate(p)

    if p.insider_pct > 0:
        p.notes.append(f"{p.insider_pct:.0f}% held by flagged insiders")
    if p.top10_pct > 0:
        p.notes.append(f"top 10 hold {p.top10_pct:.0f}%")
    return p


def _validate(p: HolderProfile) -> None:
    """Refuse to publish, or act on, a share of supply above 100%.

    Nothing downstream can distinguish a bad number from a bad token, so an
    impossible reading is caught here and disarmed rather than passed along as
    a rejection reason.
    """
    worst = max(p.top10_pct, p.top1_pct, p.insider_pct)
    if worst <= 100.5:
        return
    log.warning("holder shares read as %.0f%% of supply — report unreadable, "
                "concentration gates stood down for this token", worst)
    p.reliable = False
    p.notes.append("holder shares unreadable — concentration not judged")
    p.top10_pct = min(p.top10_pct, 100.0)
    p.top1_pct = min(p.top1_pct, 100.0)
    p.insider_pct = min(p.insider_pct, 100.0)


def fetch_profile(mint: str) -> HolderProfile:
    """Full RugCheck report → quantified holder structure."""
    return parse_report(_get("rc", f"{RC}/tokens/{mint}/report"))


def apply_gates(token, profile: HolderProfile, cfg: dict) -> tuple[bool, str]:
    """Concentration gates. Returns (passes, reason).

    These run in addition to the wash and rug gates. The $PENSION case — heavy
    insider ownership with no tracked-wallet confirmation — is cut here.
    """
    if not profile.available:
        return True, ""  # absence handled by the unknown-rug penalty upstream

    # A holder count is a count: it survives a percentage we could not read.
    if profile.holder_count and profile.holder_count < cfg["min_holders"]:
        return False, f"only {profile.holder_count} holders"

    if not profile.reliable:
        return True, ""  # unreadable is not the same thing as guilty

    if profile.insider_pct >= cfg["insider_reject_pct"]:
        return False, f"{profile.insider_pct:.0f}% insider-held"
    if profile.top10_pct >= cfg["top10_reject_pct"]:
        return False, f"top 10 hold {profile.top10_pct:.0f}%"
    if profile.top1_pct >= cfg["top1_reject_pct"]:
        return False, f"single holder at {profile.top1_pct:.0f}%"

    # Elevated-but-not-fatal concentration requires tracked-wallet confirmation.
    # A crowded cap table with nobody credible buying is the classic exit-liquidity
    # setup: the structure alone is not proof of fraud, but it removes the benefit
    # of the doubt.
    if (profile.insider_pct >= cfg["insider_needs_kol_pct"]
            and token.kol_buyers < cfg["insider_kol_required"]):
        return False, (f"{profile.insider_pct:.0f}% insiders and "
                       f"{token.kol_buyers} tracked wallets")
    return True, ""
