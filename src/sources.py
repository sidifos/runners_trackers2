"""API clients — all free, keyless sources, plus optional Helius.

No key is required for Dexscreener, GeckoTerminal and RugCheck.
HELIUS_API_KEY (optional) unlocks tracked-wallet confluence.
"""
from __future__ import annotations

import os
import time
import threading
import logging
from typing import Any, Iterable

import requests

log = logging.getLogger("sources")

UA = {"User-Agent": "runner-tracker/1.0", "Accept": "application/json"}

DS = "https://api.dexscreener.com"
GT = "https://api.geckoterminal.com/api/v2"
RC = "https://api.rugcheck.xyz/v1"
HELIUS = "https://api.helius.xyz/v0"

SOL_MINT = "So11111111111111111111111111111111111111112"
STABLES = {
    SOL_MINT,
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
    "27G8MtK7VtTcCHkpASjSDdkWWYfoqT6ggEuKidVJidD4",  # JLP
}


class RateLimiter:
    """Simple thread-safe sliding-window limiter."""

    def __init__(self, calls_per_minute: int):
        self.interval = 60.0 / max(calls_per_minute, 1)
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now < self._next:
                time.sleep(self._next - now)
                now = time.monotonic()
            self._next = now + self.interval


_limits = {
    "ds": RateLimiter(55),       # docs: 60/min across all endpoints
    "gt": RateLimiter(28),       # docs: 30/min keyless
    "rc": RateLimiter(55),
    "pf": RateLimiter(60),       # pump.fun public frontend API
    "helius": RateLimiter(540),  # 10 req/s on the Developer plan
}

_session = requests.Session()
_session.headers.update(UA)


def _get(bucket: str, url: str, *, params: dict | None = None,
         tries: int = 3, timeout: int = 25) -> Any:
    """GET with rate limiting, exponential retry and 429 handling."""
    last: Exception | None = None
    for attempt in range(tries):
        _limits[bucket].wait()
        try:
            r = _session.get(url, params=params, timeout=timeout)
            if r.status_code == 429:
                back = float(r.headers.get("Retry-After", 2 ** (attempt + 2)))
                log.warning("429 on %s — backing off %.0fs", url, back)
                time.sleep(min(back, 60))
                continue
            if r.status_code == 404:
                return None
            r.raise_for_status()
            if not r.content:
                return None
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < tries - 1:
                time.sleep(1.5 * (attempt + 1))
    log.warning("gave up on %s: %s", url, last)
    return None


# ---------------------------------------------------------------- Dexscreener

def ds_boosted_tokens() -> list[dict]:
    """Tokens that paid for promotion — a proxy for marketing budget."""
    out: list[dict] = []
    for path in ("/token-boosts/top/v1", "/token-boosts/latest/v1"):
        data = _get("ds", DS + path)
        if isinstance(data, list):
            out.extend(d for d in data if d.get("chainId") == "solana")
    return out


def ds_token_profiles() -> list[dict]:
    """Recently filled-in profiles (site, X, Telegram, description)."""
    data = _get("ds", DS + "/token-profiles/latest/v1")
    if not isinstance(data, list):
        return []
    return [d for d in data if d.get("chainId") == "solana"]


def ds_pairs_for_tokens(mints: Iterable[str]) -> list[dict]:
    """Batch enrichment, 30 mints per call (endpoint limit)."""
    mints = [m for m in dict.fromkeys(mints) if m]
    pairs: list[dict] = []
    for i in range(0, len(mints), 30):
        chunk = ",".join(mints[i:i + 30])
        data = _get("ds", f"{DS}/tokens/v1/solana/{chunk}")
        if isinstance(data, list):
            pairs.extend(data)
        elif isinstance(data, dict) and isinstance(data.get("pairs"), list):
            pairs.extend(data["pairs"])
    return pairs


def ds_trending_metas() -> list[dict]:
    """Dexscreener's ranked metas/narratives — the core of the narrative module."""
    data = _get("ds", DS + "/metas/trending/v1")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("metas", "data", "results"):
            if isinstance(data.get(k), list):
                return data[k]
    return []


def ds_meta_detail(slug: str) -> dict | None:
    data = _get("ds", f"{DS}/metas/meta/v1/{slug}")
    return data if isinstance(data, dict) else None


# -------------------------------------------------------------- GeckoTerminal

def _gt_pools(path: str, pages: int = 2) -> list[dict]:
    out: list[dict] = []
    for page in range(1, pages + 1):
        data = _get("gt", GT + path, params={"page": page})
        if not isinstance(data, dict):
            break
        items = data.get("data") or []
        if not items:
            break
        out.extend(items)
    return out


def gt_trending_pools() -> list[dict]:
    return _gt_pools("/networks/solana/trending_pools", pages=2)


def gt_new_pools() -> list[dict]:
    return _gt_pools("/networks/solana/new_pools", pages=2)


def gt_pool_mints(pools: list[dict]) -> list[str]:
    """Extract base-token mints, dropping SOL and stablecoins."""
    mints: list[str] = []
    for p in pools:
        rel = (p.get("relationships") or {}).get("base_token", {}).get("data", {})
        pid = rel.get("id", "")
        mint = pid.split("_", 1)[1] if "_" in pid else ""
        if mint and mint not in STABLES:
            mints.append(mint)
    return mints


# ------------------------------------------------------------------ RugCheck

def rugcheck_summary(mint: str) -> dict | None:
    """Condensed risk report. Public endpoint, no key."""
    return _get("rc", f"{RC}/tokens/{mint}/report/summary")


# ---------------------------------------------------------- Helius (optional)

def helius_key() -> str | None:
    k = os.environ.get("HELIUS_API_KEY", "").strip()
    return k or None


def helius_wallet_swaps(wallet: str, limit: int = 100) -> list[dict]:
    """Recent enriched transactions for a wallet. ~1 call per wallet."""
    key = helius_key()
    if not key:
        return []
    data = _get(
        "helius",
        f"{HELIUS}/addresses/{wallet}/transactions",
        params={"api-key": key, "limit": limit, "type": "SWAP"},
        timeout=30,
    )
    return data if isinstance(data, list) else []
