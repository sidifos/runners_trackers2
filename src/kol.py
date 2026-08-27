"""Tracked-wallet confluence.

The principle: volume is cheap to fake, the composition of an address book is
not. If 7 wallets you have followed for months buy the same mint inside the same
window, that is the most expensive signal on the market to simulate.

Requires HELIUS_API_KEY. Without a key the module disables itself cleanly and
the rest of the pipeline keeps running.
"""
from __future__ import annotations

import csv
import logging
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from sources import STABLES, helius_key, helius_wallet_swaps

log = logging.getLogger("kol")


def load_wallets(path: str | Path) -> list[dict]:
    """Expected CSV: address[,label][,tier][,weight]. Only address is required."""
    p = Path(path)
    if not p.exists():
        log.info("no wallet list at %s", p)
        return []

    wallets: list[dict] = []
    with p.open(newline="", encoding="utf-8") as fh:
        sample = fh.read(2048)
        fh.seek(0)
        has_header = csv.Sniffer().has_header(sample) if sample.strip() else False
        reader = csv.DictReader(fh) if has_header else csv.reader(fh)

        if has_header:
            for row in reader:  # type: ignore[assignment]
                addr = (row.get("address") or row.get("wallet") or "").strip()
                if not _plausible(addr):
                    continue
                wallets.append({
                    "address": addr,
                    "label": (row.get("label") or row.get("name") or addr[:4]).strip(),
                    "tier": (row.get("tier") or "").strip(),
                    "weight": _weight(row.get("weight"), row.get("tier")),
                })
        else:
            for row in reader:  # type: ignore[assignment]
                if not row:
                    continue
                addr = row[0].strip()
                if not _plausible(addr):
                    continue
                wallets.append({
                    "address": addr,
                    "label": (row[1].strip() if len(row) > 1 else addr[:4]),
                    "tier": "",
                    "weight": 1.0,
                })

    seen: set[str] = set()
    uniq = [w for w in wallets if not (w["address"] in seen or seen.add(w["address"]))]
    log.info("%d wallets loaded", len(uniq))
    return uniq


def _plausible(addr: str) -> bool:
    """Coarse base58 Solana address check."""
    return 32 <= len(addr) <= 44 and addr.isalnum() and "0" not in addr[:1]


def _weight(raw, tier) -> float:
    try:
        return max(float(raw), 0.1)
    except (TypeError, ValueError):
        pass
    return {"s": 3.0, "a": 2.0, "b": 1.5, "1": 3.0, "2": 2.0, "3": 1.0}.get(
        str(tier or "").strip().lower(), 1.0
    )


def collect_buys(wallets: list[dict], window_hours: int,
                 max_workers: int = 8) -> dict[str, dict]:
    """Return {mint: {"buyers": n, "weight": w, "labels": [...]}}.

    An acquisition is a tracked wallet receiving a non-stable mint inside a SWAP
    transaction. Plain incoming transfers (airdrops, dust) are excluded because
    the API call already filters on type=SWAP.
    """
    if not helius_key():
        log.warning("HELIUS_API_KEY missing — KOL confluence disabled")
        return {}
    if not wallets:
        return {}

    cutoff = time.time() - window_hours * 3600
    agg: dict[str, dict] = defaultdict(
        lambda: {"buyers": 0, "weight": 0.0, "labels": []}
    )

    def work(w: dict) -> tuple[dict, set[str]]:
        mints: set[str] = set()
        for tx in helius_wallet_swaps(w["address"]):
            if tx.get("timestamp", 0) < cutoff:
                continue
            for tr in tx.get("tokenTransfers") or []:
                if tr.get("toUserAccount") != w["address"]:
                    continue
                mint = tr.get("mint")
                if not mint or mint in STABLES:
                    continue
                try:
                    if float(tr.get("tokenAmount") or 0) <= 0:
                        continue
                except (TypeError, ValueError):
                    continue
                mints.add(mint)
        return w, mints

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(work, w) for w in wallets]
        for i, fut in enumerate(as_completed(futures), 1):
            try:
                w, mints = fut.result()
            except Exception as e:  # noqa: BLE001
                log.warning("wallet failed: %s", e)
                continue
            for mint in mints:
                entry = agg[mint]
                entry["buyers"] += 1
                entry["weight"] += w["weight"]
                if len(entry["labels"]) < 8:
                    entry["labels"].append(w["label"])
            if i % 50 == 0:
                log.info("  %d/%d wallets processed", i, len(futures))

    log.info("confluence computed across %d distinct mints", len(agg))
    return dict(agg)


def apply_to_tokens(tokens, buys: dict[str, dict]) -> None:
    for t in tokens:
        hit = buys.get(t.mint)
        if hit:
            t.kol_buyers = hit["buyers"]
            t.kol_weight = round(hit["weight"], 2)
            t.kol_names = hit["labels"]
