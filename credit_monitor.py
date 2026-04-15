"""
Track Anthropic token usage per run, accumulate daily cost,
and alert when the daily budget threshold is approaching or exceeded.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from config import (
    ANTHROPIC_DAILY_BUDGET_USD,
    CLAUDE_INPUT_COST_PER_MTOK,
    CLAUDE_OUTPUT_COST_PER_MTOK,
    DATA_DIR,
)

logger = logging.getLogger(__name__)

USAGE_FILE = DATA_DIR / "token_usage.json"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load() -> dict:
    try:
        with open(str(USAGE_FILE)) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(data: dict):
    with open(str(USAGE_FILE), "w") as f:
        json.dump(data, f, indent=2)


def _today_key() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")


def cost_usd(input_tokens: int, output_tokens: int) -> float:
    return (
        input_tokens  / 1_000_000 * CLAUDE_INPUT_COST_PER_MTOK
        + output_tokens / 1_000_000 * CLAUDE_OUTPUT_COST_PER_MTOK
    )


# ── Public API ────────────────────────────────────────────────────────────────

def record_usage(input_tokens: int, output_tokens: int, label: str = ""):
    """
    Add one API call's token counts to today's running total.
    Returns (today_total_usd, budget_remaining_usd).
    """
    data = _load()
    today = _today_key()

    day = data.setdefault(today, {"input_tokens": 0, "output_tokens": 0, "calls": 0})
    day["input_tokens"]  += input_tokens
    day["output_tokens"] += output_tokens
    day["calls"]         += 1
    _save(data)

    today_cost      = cost_usd(day["input_tokens"], day["output_tokens"])
    budget_remaining = ANTHROPIC_DAILY_BUDGET_USD - today_cost

    if label:
        logger.debug(
            "Token usage [%s]: in=%d out=%d → +$%.4f  |  today=$%.4f  remaining=$%.4f",
            label, input_tokens, output_tokens,
            cost_usd(input_tokens, output_tokens),
            today_cost, budget_remaining,
        )
    return today_cost, budget_remaining


def get_today_summary() -> dict:
    """Return today's usage summary."""
    data = _load()
    today = _today_key()
    day = data.get(today, {"input_tokens": 0, "output_tokens": 0, "calls": 0})
    usd = cost_usd(day["input_tokens"], day["output_tokens"])
    return {
        "date":           today,
        "input_tokens":   day["input_tokens"],
        "output_tokens":  day["output_tokens"],
        "calls":          day["calls"],
        "cost_usd":       usd,
        "budget_usd":     ANTHROPIC_DAILY_BUDGET_USD,
        "remaining_usd":  ANTHROPIC_DAILY_BUDGET_USD - usd,
        "pct_used":       min(100.0, usd / ANTHROPIC_DAILY_BUDGET_USD * 100) if ANTHROPIC_DAILY_BUDGET_USD else 0,
    }


def check_budget_alert() -> tuple:
    """
    Check if today's spend has crossed a warning or hard-stop threshold.
    Returns (alert_level, message) where alert_level is:
        None      — all clear
        "warning" — past 80% of daily budget
        "danger"  — at or over 100% of daily budget
    """
    s = get_today_summary()
    pct = s["pct_used"]

    if pct >= 100:
        msg = (
            f"Anthropic daily budget EXHAUSTED\n"
            f"Spent: ${s['cost_usd']:.4f} of ${s['budget_usd']:.2f} limit\n"
            f"API calls today: {s['calls']}\n"
            f"Document generation paused to protect your credits.\n"
            f"Top-up at: console.anthropic.com → Billing"
        )
        return "danger", msg

    if pct >= 80:
        msg = (
            f"Anthropic credit warning — {pct:.0f}% of daily budget used\n"
            f"Spent: ${s['cost_usd']:.4f} of ${s['budget_usd']:.2f}\n"
            f"Remaining: ${s['remaining_usd']:.4f}\n"
            f"Top-up or raise limit in config.py → ANTHROPIC_DAILY_BUDGET_USD"
        )
        return "warning", msg

    return None, ""


def get_weekly_report() -> str:
    """Return a 7-day cost summary string for notifications."""
    data = _load()
    from datetime import timedelta
    lines = ["Anthropic spend — last 7 days:"]
    total = 0.0
    for i in range(7):
        day = (datetime.now(tz=timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
        d = data.get(day, {})
        usd = cost_usd(d.get("input_tokens", 0), d.get("output_tokens", 0))
        total += usd
        lines.append(f"  {day}: ${usd:.4f}  ({d.get('calls', 0)} calls)")
    lines.append(f"  TOTAL 7d: ${total:.4f}")
    return "\n".join(lines)
