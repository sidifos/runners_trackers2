"""Causal synthesis — turning evidence into a specific reason.

Given the evidence bundle from research.py, produce a concrete claim about why a
token moved, plus a narrative tag that is actually tradeable ("Korean exchange
listing rumour", "a Tier-S wallet cluster bought within 8 minutes") rather than
a category ("animals").

Uses the Anthropic API when ANTHROPIC_API_KEY is set. Without a key it falls
back to a deterministic rule engine — less nuanced, never wrong about the facts,
and it keeps the pipeline running.

Accumulated lessons from postmortem.py are injected into the prompt, so the
analysis of what matters shifts as the market teaches the system.
"""
from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor

import requests

log = logging.getLogger("synthesis")

API_URL = "https://api.anthropic.com/v1/messages"
MODEL_CANDIDATES = [
    "claude-sonnet-4-5",
    "claude-sonnet-4-20250514",
    "claude-3-7-sonnet-latest",
    "claude-3-5-sonnet-latest",
]
_resolved_model: str | None = None

SYSTEM = """You analyse Solana memecoin price action for a market-data tracker.

Given evidence about one token, state the most likely reason it moved. You are
writing for a trader who will publish this, so:

- Be specific. "Animal meme" is not a reason. "Launched 14h ago, hit king-of-hill
  in 40min, 6 tracked wallets bought inside 11 minutes" is a reason.
- Rank causes by what the evidence supports, not by what sounds good.
- If the evidence does not explain the move, say so. "Unexplained move on thin
  liquidity" is a valid and useful answer.
- Name the risk that would most plausibly kill this token in the next 24h.
- Never invent facts. Only use what is in the evidence.

Return strict JSON, no prose outside it:
{
  "why_ran": "<2-3 sentences, specific, evidence-grounded>",
  "narrative_tag": "<short specific tag, max 6 words>",
  "primary_driver": "<one of: kol_cluster|organic_attention|paid_promotion|
                      launch_mechanics|sector_rotation|insider_accumulation|
                      unexplained>",
  "confidence": <0-100 integer>,
  "main_risk": "<one sentence>",
  "evidence_gaps": "<what you would need to be sure, one sentence>"
}"""


def api_key() -> str | None:
    k = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    return k or None


def _call(prompt: str, max_tokens: int = 900) -> str | None:
    """One Anthropic call, resolving the model on first use."""
    global _resolved_model
    key = api_key()
    if not key:
        return None
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    models = [_resolved_model] if _resolved_model else MODEL_CANDIDATES
    for model in models:
        if not model:
            continue
        try:
            r = requests.post(API_URL, headers=headers, timeout=90, json={
                "model": model,
                "max_tokens": max_tokens,
                "system": SYSTEM,
                "messages": [{"role": "user", "content": prompt}],
            })
            if r.status_code == 404:
                log.warning("model %s unavailable, trying next", model)
                continue
            if r.status_code == 429:
                log.warning("rate limited by the Anthropic API")
                return None
            r.raise_for_status()
            _resolved_model = model
            blocks = r.json().get("content") or []
            return "".join(b.get("text", "") for b in blocks if isinstance(b, dict))
        except Exception as e:  # noqa: BLE001
            log.warning("synthesis call failed on %s: %s", model, e)
            continue
    return None


def _parse(raw: str | None) -> dict | None:
    if not raw:
        return None
    text = raw.strip()
    if "```" in text:
        text = text.split("```")[1]
        text = text[4:] if text.lower().startswith("json") else text
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or "why_ran" not in obj:
        return None
    try:
        obj["confidence"] = max(0, min(100, int(obj.get("confidence", 50))))
    except (TypeError, ValueError):
        obj["confidence"] = 50
    return obj


# ------------------------------------------------------------- rule fallback

def rule_based(ev: dict) -> dict:
    """Deterministic explanation when no API key is configured."""
    kol, att = ev.get("kol", {}), ev.get("attention", {})
    shape, launch = ev.get("shape", {}), ev.get("launch", {})
    holders, market = ev.get("holders", {}), ev.get("market", {})
    reasons, driver, conf = [], "unexplained", 25

    n = kol.get("kol_buyers", 0)
    cluster = kol.get("cluster_minutes")
    if n >= 3:
        driver, conf = "kol_cluster", 65
        line = f"{n} tracked wallets bought this"
        if cluster is not None and cluster < 60:
            line += f", all within {cluster:.0f} minutes of each other"
            conf = 75
        reasons.append(line + ".")

    rph = att.get("replies_per_hour", 0)
    if rph and rph > 20:
        reasons.append(f"pump.fun comments running at {rph:.0f}/hour.")
        if driver == "unexplained":
            driver, conf = "organic_attention", 55

    if att.get("paid_boosts"):
        reasons.append(f"{att['paid_boosts']} paid Dexscreener boost(s) active.")
        if driver == "unexplained":
            driver, conf = "paid_promotion", 50

    if launch.get("reached_king_of_hill") and ev.get("age_hours", 999) < 48:
        reasons.append("reached king-of-the-hill on pump.fun.")
        if driver == "unexplained":
            driver, conf = "launch_mechanics", 45

    if holders.get("insider_pct", 0) > 15:
        reasons.append(f"{holders['insider_pct']:.0f}% sits with flagged insiders.")
        if driver == "unexplained":
            driver, conf = "insider_accumulation", 40

    metas = ev.get("matching_metas") or []
    if metas and driver == "unexplained":
        driver, conf = "sector_rotation", 40
        reasons.append(f"belongs to the {metas[0]['name']} meta, "
                       f"{metas[0]['chg_h6']:+.0f}% on 6h.")

    if not reasons:
        reasons.append(f"moved {market.get('chg_h24', 0):+.0f}% with no "
                       "attributable catalyst in the available data.")

    risk = "Thin liquidity relative to the move."
    if holders.get("insider_pct", 0) > 20:
        risk = f"{holders['insider_pct']:.0f}% insider supply can be distributed at any time."
    elif shape.get("cooling_off"):
        risk = "Already rolling over on the 1h — the move may be done."
    elif not att.get("has_twitter"):
        risk = "No social presence to sustain attention past the initial move."

    return {
        "why_ran": " ".join(r.capitalize() if i == 0 else r
                            for i, r in enumerate(reasons)),
        "narrative_tag": (metas[0]["name"] if metas else
                          driver.replace("_", " ")),
        "primary_driver": driver,
        "confidence": conf,
        "main_risk": risk,
        "evidence_gaps": "No language-model analysis configured; "
                         "explanation is rule-based only.",
        "source": "rules",
    }


# ---------------------------------------------------------------- public API

def analyse_one(ev: dict, lessons: list[str]) -> dict:
    lesson_block = ""
    if lessons:
        lesson_block = ("\nLessons learned from this tracker's own past calls "
                        "(weight these):\n- " + "\n- ".join(lessons[:12]) + "\n")
    prompt = (f"Evidence:\n{json.dumps(ev, ensure_ascii=False, indent=1)}\n"
              f"{lesson_block}\nReturn the JSON object.")
    parsed = _parse(_call(prompt))
    if parsed:
        parsed["source"] = "llm"
        return parsed
    return rule_based(ev)


def analyse(evidence: dict[str, dict], lessons: list[str],
            max_workers: int = 3) -> dict[str, dict]:
    mode = "language model" if api_key() else "rule engine"
    log.info("synthesising causes for %d runners via %s", len(evidence), mode)
    mints = list(evidence)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        results = list(pool.map(lambda m: analyse_one(evidence[m], lessons), mints))
    return dict(zip(mints, results))


def daily_narrative(evidence: dict[str, dict], analyses: dict[str, dict],
                    metas: list[dict], lessons: list[str]) -> dict:
    """One paragraph tying the day together, above the per-token analyses."""
    if not evidence:
        return {"summary": "No runners cleared the filters this run.",
                "rotation": "", "source": "rules"}

    drivers: dict[str, int] = {}
    for a in analyses.values():
        drivers[a.get("primary_driver", "unexplained")] = \
            drivers.get(a.get("primary_driver", "unexplained"), 0) + 1
    top_driver = max(drivers, key=drivers.get) if drivers else "unexplained"

    if api_key():
        compact = [
            {"symbol": ev["symbol"], "chg_h24": ev["market"]["chg_h24"],
             "mcap": ev["market"]["mcap"], "kol_buyers": ev["kol"]["kol_buyers"],
             "why": analyses.get(m, {}).get("why_ran", ""),
             "tag": analyses.get(m, {}).get("narrative_tag", "")}
            for m, ev in evidence.items()
        ]
        prompt = (
            "Here are today's runners with the reason each one moved, plus the "
            "market's own meta ranking.\n\n"
            f"Runners: {json.dumps(compact, ensure_ascii=False)}\n\n"
            f"Metas: {json.dumps(metas[:8], ensure_ascii=False)}\n"
            + (f"\nPast lessons:\n- " + "\n- ".join(lessons[:10]) if lessons else "")
            + "\n\nReturn JSON only:\n"
            '{"summary": "<3-4 sentences: what actually drove today\'s board, '
            'what the common thread is, and what it means for tomorrow>", '
            '"rotation": "<one sentence: what money is rotating out of and into>", '
            '"watch_next": "<one sentence: the most specific thing to watch>"}'
        )
        parsed = _parse(_call(prompt, max_tokens=700))
        if parsed and "summary" in parsed:
            parsed["source"] = "llm"
            parsed["driver_mix"] = drivers
            return parsed

    labels = {
        "kol_cluster": "tracked-wallet clusters", "organic_attention": "organic attention",
        "paid_promotion": "paid promotion", "launch_mechanics": "launch mechanics",
        "sector_rotation": "sector rotation", "insider_accumulation": "insider accumulation",
        "unexplained": "no identifiable catalyst",
    }
    lead = metas[0] if metas else None
    return {
        "summary": (f"{drivers.get(top_driver, 0)} of {len(analyses)} runners were "
                    f"driven by {labels.get(top_driver, top_driver)}."
                    + (f" The market's fastest meta is {lead['name']} at "
                       f"{lead['chg_h6']:+.0f}% on 6h." if lead else "")),
        "rotation": (f"{lead['name']} is absorbing the flow."
                     if lead else ""),
        "watch_next": "",
        "driver_mix": drivers,
        "source": "rules",
    }
