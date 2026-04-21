"""
Track Anthropic token usage and Apify compute unit usage per run.

Two-level budget system:
  1. Account balance:  cumulative all-time spend vs ANTHROPIC_TOTAL_CREDIT_USD
                       → shows how much credit you actually have left
  2. Daily soft-cap:   per-day spend vs ANTHROPIC_DAILY_BUDGET_USD
                       → safety guard to avoid blowing the whole account in one day

Both are tracked locally in data/token_usage.json.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests as _requests

from config import (
    ANTHROPIC_API_KEY,
    ANTHROPIC_DAILY_BUDGET_USD,
    ANTHROPIC_TOTAL_CREDIT_USD,
    APIFY_API_TOKEN,
    CLAUDE_INPUT_COST_PER_MTOK,
    CLAUDE_OUTPUT_COST_PER_MTOK,
    DATA_DIR,
)

logger = logging.getLogger(__name__)

USAGE_FILE = DATA_DIR / "token_usage.json"


# ── File helpers ──────────────────────────────────────────────────────────────

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
    Add one API call's token counts to today's running total
    and to the all-time cumulative total.
    Returns (today_total_usd, account_remaining_usd).
    """
    data  = _load()
    today = _today_key()

    # Per-day bucket
    day = data.setdefault(today, {"input_tokens": 0, "output_tokens": 0, "calls": 0})
    day["input_tokens"]  += input_tokens
    day["output_tokens"] += output_tokens
    day["calls"]         += 1

    # All-time cumulative bucket
    cumul = data.setdefault("_cumulative", {"input_tokens": 0, "output_tokens": 0, "calls": 0})
    cumul["input_tokens"]  += input_tokens
    cumul["output_tokens"] += output_tokens
    cumul["calls"]         += 1

    _save(data)

    today_cost        = cost_usd(day["input_tokens"], day["output_tokens"])
    cumul_cost        = cost_usd(cumul["input_tokens"], cumul["output_tokens"])
    account_remaining = max(0.0, ANTHROPIC_TOTAL_CREDIT_USD - cumul_cost)

    if label:
        logger.debug(
            "Token usage [%s]: in=%d out=%d → +$%.4f  |  today=$%.4f  "
            "total=$%.4f  account_remaining=$%.4f",
            label, input_tokens, output_tokens,
            cost_usd(input_tokens, output_tokens),
            today_cost, cumul_cost, account_remaining,
        )
    return today_cost, account_remaining


def get_today_summary() -> dict:
    """Return today's usage summary AND the account-level remaining balance."""
    data  = _load()
    today = _today_key()
    day   = data.get(today, {"input_tokens": 0, "output_tokens": 0, "calls": 0})
    cumul = data.get("_cumulative", {"input_tokens": 0, "output_tokens": 0, "calls": 0})

    today_cost  = cost_usd(day["input_tokens"],   day["output_tokens"])
    cumul_cost  = cost_usd(cumul["input_tokens"], cumul["output_tokens"])
    account_rem = max(0.0, ANTHROPIC_TOTAL_CREDIT_USD - cumul_cost)
    account_pct = min(100.0, cumul_cost / ANTHROPIC_TOTAL_CREDIT_USD * 100) if ANTHROPIC_TOTAL_CREDIT_USD else 0

    return {
        # ── Per-day ──────────────────────────────────────────────────────────
        "date":           today,
        "input_tokens":   day["input_tokens"],
        "output_tokens":  day["output_tokens"],
        "calls":          day["calls"],
        "cost_usd":       today_cost,
        "budget_usd":     ANTHROPIC_DAILY_BUDGET_USD,
        "remaining_usd":  max(0.0, ANTHROPIC_DAILY_BUDGET_USD - today_cost),
        "pct_used":       min(100.0, today_cost / ANTHROPIC_DAILY_BUDGET_USD * 100) if ANTHROPIC_DAILY_BUDGET_USD else 0,
        # ── Account-level ─────────────────────────────────────────────────────
        "account_total_usd":    ANTHROPIC_TOTAL_CREDIT_USD,
        "account_spent_usd":    cumul_cost,
        "account_remaining_usd": account_rem,
        "account_pct_used":     account_pct,
        "total_calls_ever":     cumul["calls"],
    }


def check_budget_alert() -> tuple:
    """
    Check spend thresholds. Returns (alert_level, message):
        None      — all clear
        "warning" — account 80% depleted OR today >80% of daily cap
        "danger"  — account 95%+ depleted OR today >100% of daily cap
    """
    s   = get_today_summary()
    pct = s["pct_used"]          # daily cap %
    a   = s["account_pct_used"]  # account %

    # Account level — higher priority
    if a >= 95:
        msg = (
            f"⚠️ Anthropic account NEARLY EXHAUSTED\n"
            f"Account: ${s['account_spent_usd']:.2f} spent / ${s['account_total_usd']:.2f} total\n"
            f"Remaining: <b>${s['account_remaining_usd']:.2f}</b> ({100-a:.0f}% left)\n"
            f"Top-up at: console.anthropic.com → Billing\n"
            f"Or update ANTHROPIC_TOTAL_CREDIT_USD in config.py / GitHub Secrets."
        )
        return "danger", msg

    if a >= 80:
        msg = (
            f"Account balance warning — {a:.0f}% of ${s['account_total_usd']:.2f} credit used\n"
            f"Spent: ${s['account_spent_usd']:.2f} total  |  "
            f"Remaining: ${s['account_remaining_usd']:.2f}"
        )
        return "warning", msg

    # Daily cap level
    if pct >= 100:
        msg = (
            f"Daily cap hit (${s['budget_usd']:.2f}/day limit)\n"
            f"Today: ${s['cost_usd']:.4f} in {s['calls']} calls\n"
            f"Account balance still OK: ${s['account_remaining_usd']:.2f} remaining of "
            f"${s['account_total_usd']:.2f} total."
        )
        return "danger", msg

    if pct >= 80:
        msg = (
            f"Daily cap warning — {pct:.0f}% used today\n"
            f"Today: ${s['cost_usd']:.4f} / ${s['budget_usd']:.2f} cap\n"
            f"Account: ${s['account_remaining_usd']:.2f} remaining."
        )
        return "warning", msg

    return None, ""


def get_apify_usage() -> Optional[dict]:
    """
    Fetch current Apify account usage via the Apify API.
    Returns a dict with keys: used_usd, limit_usd, remaining_usd, pct_used.
    Returns None on failure.
    """
    if not APIFY_API_TOKEN:
        return None
    try:
        r = _requests.get(
            "https://api.apify.com/v2/users/me",
            headers={"Authorization": f"Bearer {APIFY_API_TOKEN}"},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json().get("data", {})
        plan = data.get("plan", {})

        used_usd  = float(plan.get("monthlyUsageCreditsUsd", 0) or 0)
        limit_usd = float(plan.get("monthlyUsageCreditLimitUsd", 5) or 5)
        remaining = max(0.0, limit_usd - used_usd)
        pct_used  = min(100.0, used_usd / limit_usd * 100) if limit_usd else 0

        return {
            "used_usd":      used_usd,
            "limit_usd":     limit_usd,
            "remaining_usd": remaining,
            "pct_used":      pct_used,
        }
    except Exception as exc:
        logger.warning("Apify usage fetch failed: %s", exc)
        return None


def get_weekly_report() -> str:
    """Return a 7-day cost breakdown + account balance summary."""
    data  = _load()
    cumul = data.get("_cumulative", {"input_tokens": 0, "output_tokens": 0, "calls": 0})
    from datetime import timedelta
    lines = ["Anthropic spend — last 7 days:"]
    week_total = 0.0
    for i in range(7):
        day = (datetime.now(tz=timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
        d   = data.get(day, {})
        usd = cost_usd(d.get("input_tokens", 0), d.get("output_tokens", 0))
        week_total += usd
        lines.append(f"  {day}: ${usd:.4f}  ({d.get('calls', 0)} calls)")
    lines.append(f"  7-day total: ${week_total:.4f}")
    cumul_cost = cost_usd(cumul["input_tokens"], cumul["output_tokens"])
    account_rem = max(0.0, ANTHROPIC_TOTAL_CREDIT_USD - cumul_cost)
    lines.append(f"\nAccount: ${cumul_cost:.2f} spent / ${ANTHROPIC_TOTAL_CREDIT_USD:.2f} total → ${account_rem:.2f} remaining")
    return "\n".join(lines)
