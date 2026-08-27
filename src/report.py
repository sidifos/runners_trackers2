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

DRIVER_LABELS = {
    "kol_cluster": "tracked-wallet cluster",
    "organic_attention": "organic attention",
    "paid_promotion": "paid promotion",
    "launch_mechanics": "launch mechanics",
    "sector_rotation": "sector rotation",
    "insider_accumulation": "insider accumulation",
    "unexplained": "no clear catalyst",
}


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


def driver_label(key: str) -> str:
    return DRIVER_LABELS.get(key or "", (key or "").replace("_", " "))


def trust_label(t) -> tuple[str, str]:
    """Trust badge: status plus label — never colour alone."""
    insider = (t.holders or {}).get("insider_pct", 0)
    worst = max(t.wash_score, t.rug_score, insider * 1.6)
    if worst < 20:
        return "good", "clean"
    if worst < 40:
        return "warning", "watch"
    if worst < 55:
        return "serious", "shaky"
    return "critical", "dangerous"


def verdict_label(label: str) -> tuple[str, str]:
    return {
        "good": ("good", "up"),
        "flat": ("warning", "flat"),
        "bad": ("serious", "down"),
        "dead": ("critical", "gone"),
    }.get(label, ("warning", label))


def build_threads(payload: dict) -> list[dict]:
    """Post drafts built from this run's actual numbers and reasoning."""
    runners = payload["runners"]
    narr = payload["narrative"]
    stats = payload["stats"]
    pm = payload.get("postmortem") or {}
    when = payload["generated_at"].astimezone(PARIS).strftime("%b %d")
    threads = []

    if runners:
        lines = [f"Solana runners — {when}", ""]
        for i, t in enumerate(runners[:5], 1):
            tag = t.analysis.get("narrative_tag", "")
            lines.append(f"{i}. ${t.symbol} · {fmt_pct(t.chg_h24)} · "
                         f"vol {fmt_usd(t.vol_h24)}"
                         + (f" · {tag}" if tag else ""))
        lines += ["", f"Out of {stats['scanned']} tokens scanned, "
                      f"{stats['rejected']} were cut for manufactured volume, "
                      f"insider concentration or structural risk.",
                  "", "Why each one moved ↓"]
        threads.append({"title": "Today's ranking", "body": "\n".join(lines)})

    with_why = [t for t in runners if t.analysis.get("why_ran")][:3]
    if with_why:
        lines = ["Why they actually ran — not just what they are:", ""]
        for t in with_why:
            conf = t.analysis.get("confidence")
            lines.append(f"${t.symbol} — {t.analysis['why_ran']}"
                         + (f" (confidence {conf}/100)" if conf else ""))
            lines.append("")
        threads.append({"title": "The causes", "body": "\n".join(lines).strip()})

    if narr.get("daily", {}).get("rotation"):
        d = narr["daily"]
        lines = [d.get("summary", ""), ""]
        if d.get("rotation"):
            lines += [f"Rotation: {d['rotation']}", ""]
        if d.get("watch_next"):
            lines += [f"Watching: {d['watch_next']}"]
        threads.append({"title": "The narrative",
                        "body": "\n".join(lines).strip()})

    if payload.get("rejected"):
        lines = ["Charts that look like they're sending, and why I'm not touching them:", ""]
        for r in payload["rejected"][:4]:
            lines.append(f"${r['symbol']} — {fmt_pct(r['chg'])}, "
                         f"vol {fmt_usd(r['vol'])} → {r['reason']}")
        lines += ["", "Volume is cheap to fake. Holder structure is not."]
        threads.append({"title": "Today's traps", "body": "\n".join(lines)})

    sc = pm.get("scorecard")
    if sc and sc.get("reviewed", 0) >= 10:
        lines = [
            f"Scoring my own calls, {sc['reviewed']} reviewed at ~24h:", "",
            f"Up: {sc['good']}   Flat: {sc['flat']}   "
            f"Down: {sc['bad']}   Gone: {sc['dead']}",
            f"Median outcome: {sc['median_change']:+.0f}%", "",
        ]
        if pm.get("lessons"):
            lines += ["What the record actually says:", ""]
            lines += [f"— {le}" for le in pm["lessons"][:3]]
        threads.append({"title": "Accountability", "body": "\n".join(lines).strip()})

    return threads


DEFAULTS = {
    "evidence": {},
    "postmortem": {},
    "wallet_ranking": [],
    "rejected": [],
    "calibration": {},
}


def render(payload: dict, out_path: str | Path) -> Path:
    # Optional sections must never be able to break the render: a degraded run
    # should still publish the parts that did work.
    payload = {**DEFAULTS, **payload}
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

    pm = payload.get("postmortem") or {}
    verdicts = []
    if pm.get("verdicts"):
        import postmortem
        verdicts = postmortem.recent_verdicts(pm, limit=12)

    local = payload["generated_at"].astimezone(PARIS)
    html = env.get_template("dashboard.html.j2").render(
        **payload,
        threads=build_threads(payload),
        max_score=max_score,
        max_theme_vol=max_theme_vol,
        trust_label=trust_label,
        verdict_label=verdict_label,
        driver_label=driver_label,
        classify=narrative.classify_theme,
        verdicts=verdicts,
        date_str=date_en(local),
        time_str=local.strftime("%H:%M"),
        total_volume=sum(t.vol_h24 for t in runners),
    )
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
