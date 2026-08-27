"""Rendering: self-contained HTML dashboard plus X post drafts."""
from __future__ import annotations

from datetime import timezone, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

import narrative

PARIS = timezone(timedelta(hours=2))  # Europe/Paris, summer time

TEMPLATES = Path(__file__).parent / "templates"

_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
         "Saturday", "Sunday"]
_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]


def date_en(dt) -> str:
    """Date string that does not depend on the system locale."""
    return f"{_DAYS[dt.weekday()]}, {_MONTHS[dt.month - 1]} {dt.day}, {dt.year}"


def fmt_usd(v) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    for unit, div in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if abs(v) >= div:
            return f"${v / div:,.1f}{unit}"
    return f"${v:,.0f}"


def fmt_pct(v) -> str:
    try:
        return f"{float(v):+,.0f}%"
    except (TypeError, ValueError):
        return "—"


def fmt_age(hours: float) -> str:
    if hours >= 9000:
        return "—"
    if hours < 48:
        return f"{hours:.0f}h"
    if hours < 24 * 30:
        return f"{hours / 24:.0f}d"
    return f"{hours / 24 / 30:.0f}mo"


def trust_label(t) -> tuple[str, str]:
    """Trust badge: status plus label — never colour alone."""
    worst = max(t.wash_score, t.rug_score)
    if worst < 20:
        return "good", "clean"
    if worst < 40:
        return "warning", "watch"
    if worst < 55:
        return "serious", "shaky"
    return "critical", "dangerous"


def build_threads(payload: dict) -> list[dict]:
    """Post drafts built from this run's actual numbers."""
    runners = payload["runners"]
    narr = payload["narrative"]
    stats = payload["stats"]
    when = payload["generated_at"].astimezone(PARIS).strftime("%b %d")
    threads = []

    if runners:
        lines = [f"Solana runners — {when}", ""]
        for i, t in enumerate(runners[:5], 1):
            lines.append(f"{i}. ${t.symbol} · {fmt_pct(t.chg_h24)} · "
                         f"vol {fmt_usd(t.vol_h24)} · liq {fmt_usd(t.liquidity)}")
        lines += ["", f"Out of {stats['scanned']} tokens scanned, "
                      f"{stats['rejected']} were cut for manufactured volume or "
                      f"structural risk.", "", "Method in the replies ↓"]
        threads.append({"title": "Today's ranking", "body": "\n".join(lines)})

    if narr.get("themes"):
        lead = narr["themes"][0]
        lines = [
            f"Today's narrative: {lead['theme']}.", "",
            f"{lead['count']} tokens from that cluster made my runners, "
            f"{fmt_usd(lead['total_volume'])} combined volume, "
            f"median {lead['median_change']:+.0f}%.", "",
            "Tickers: " + " ".join("$" + s for s in lead["tokens"][:6]),
        ]
        if narr.get("emerging"):
            e = narr["emerging"][0]
            lines += ["", f"What's forming behind it: {e['name']} "
                          f"({e['status']}, {e['chg_h6']:+.0f}% on 6h)."]
        threads.append({"title": "The narrative", "body": "\n".join(lines)})

    if payload.get("rejected"):
        lines = ["Charts that look like they're sending, and why I'm not touching them:", ""]
        for r in payload["rejected"][:4]:
            lines.append(f"${r['symbol']} — {fmt_pct(r['chg'])}, "
                         f"vol {fmt_usd(r['vol'])} → {r['reason']}")
        lines += ["", "Volume is cheap to fake. Liquidity depth and holder "
                      "structure are not."]
        threads.append({"title": "Today's traps", "body": "\n".join(lines)})

    return threads


def render(payload: dict, out_path: str | Path) -> Path:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["usd"] = fmt_usd
    env.filters["pct"] = fmt_pct
    env.filters["age"] = fmt_age

    runners = payload["runners"]
    max_score = max((t.score for t in runners), default=1.0) or 1.0
    themes = payload["narrative"].get("themes", [])
    max_theme_vol = max((t["total_volume"] for t in themes), default=1) or 1

    local = payload["generated_at"].astimezone(PARIS)
    html = env.get_template("dashboard.html.j2").render(
        **payload,
        threads=build_threads(payload),
        max_score=max_score,
        max_theme_vol=max_theme_vol,
        trust_label=trust_label,
        classify=narrative.classify_theme,
        date_str=date_en(local),
        time_str=local.strftime("%H:%M"),
        total_volume=sum(t.vol_h24 for t in runners),
    )
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
