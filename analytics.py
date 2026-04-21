"""
analytics.py — Response rate and application analytics.

Reads tracker.xlsx and scored_jobs.jsonl to compute:
  - Application funnel: found → applied → (future: responded/interviewed/offered)
  - Applied rate by source, region, relevance score band
  - Top companies applied to
  - Weekly trend (jobs found per day)

Run manually:
    python3 analytics.py

Or triggered automatically from main.py every Sunday.
"""

import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from notifier import _send
from tracker_manager import get_all_jobs

logger = logging.getLogger(__name__)


# ── Analytics computation ─────────────────────────────────────────────────────

def generate_analytics_report() -> None:
    """Compute analytics from tracker.xlsx and send a Telegram report."""
    jobs = get_all_jobs()
    if not jobs:
        _send("📊 <b>Analytics</b>\n\nTracker is empty — no data yet.")
        return

    total       = len(jobs)
    applied     = [j for j in jobs if (j.get("Status") or "").lower() == "applied"]
    waiting     = [j for j in jobs if (j.get("Status") or "").lower() == "waiting to apply"]
    rejected    = [j for j in jobs if (j.get("Status") or "").lower() == "rejected"]

    applied_rate = f"{len(applied)/total*100:.0f}%" if total else "0%"

    # ── By source ────────────────────────────────────────────────────────────
    source_total   = Counter(j.get("Source", "?") for j in jobs)
    source_applied = Counter(j.get("Source", "?") for j in applied)

    source_lines = []
    for src, cnt in source_total.most_common():
        app_cnt = source_applied.get(src, 0)
        rate    = f"{app_cnt/cnt*100:.0f}%" if cnt else "-"
        source_lines.append(f"  {src}: {cnt} found, {app_cnt} applied ({rate})")

    # ── By region ────────────────────────────────────────────────────────────
    region_emojis = {
        "Europe":        "🇪🇺",
        "Asia":          "🌏",
        "USA_Canada":    "🇺🇸",
        "South_America": "🌎",
        "Middle_East":   "🌍",
    }
    region_total   = Counter(j.get("Region", "?") for j in jobs)
    region_applied = Counter(j.get("Region", "?") for j in applied)

    region_lines = []
    for reg, cnt in region_total.most_common():
        app_cnt = region_applied.get(reg, 0)
        rate    = f"{app_cnt/cnt*100:.0f}%" if cnt else "-"
        emoji   = region_emojis.get(reg, "🌐")
        region_lines.append(f"  {emoji} {reg}: {cnt} found, {app_cnt} applied ({rate})")

    # ── Top companies applied to ──────────────────────────────────────────────
    top_companies = Counter(j.get("Company", "?") for j in applied).most_common(5)
    company_lines = [f"  {co} ({n})" for co, n in top_companies]

    # ── Weekly trend: jobs found per day ─────────────────────────────────────
    day_counts: defaultdict = defaultdict(int)
    for j in jobs:
        date_found = j.get("Date Found", "")
        if date_found:
            try:
                day = str(date_found)[:10]   # "YYYY-MM-DD"
                day_counts[day] += 1
            except Exception:
                pass
    recent_days = sorted(day_counts.items())[-7:]  # last 7 days with data
    trend_lines = [f"  {day}: {cnt} job(s)" for day, cnt in recent_days]

    # ── Build message ─────────────────────────────────────────────────────────
    now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    parts = [
        f"📊 <b>Application Analytics</b>  <i>{now_str}</i>",
        "",
        f"<b>Funnel</b>",
        f"  Found: <b>{total}</b>  |  Applied: <b>{len(applied)}</b> ({applied_rate})"
        f"  |  Waiting: {len(waiting)}  |  Rejected: {len(rejected)}",
        "",
        f"<b>By Source</b>",
        *source_lines,
        "",
        f"<b>By Region</b>",
        *region_lines,
    ]

    if company_lines:
        parts += ["", f"<b>Top Companies Applied</b>", *company_lines]

    if trend_lines:
        parts += ["", f"<b>Recent Activity (last 7 active days)</b>", *trend_lines]

    # Tips based on data
    tips = []
    best_source = source_applied.most_common(1)
    if best_source and best_source[0][1] > 0:
        tips.append(f"Best source so far: {best_source[0][0]} ({best_source[0][1]} applied)")

    unapplied_count = len(waiting)
    if unapplied_count > 5:
        tips.append(f"You have {unapplied_count} offers waiting — review and apply before they expire!")

    if tips:
        parts += ["", f"<b>💡 Insights</b>", *[f"  {t}" for t in tips]]

    _send("\n".join(parts))
    logger.info("Analytics report sent to Telegram.")


def analyze_rejections() -> None:
    """
    Claude analysis of rejection patterns — triggered when rejection count crosses 5.
    Sends a targeted Telegram report with actionable targeting advice.
    Saves result to data/rejection_analysis.txt for dashboard display.
    """
    from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, DATA_DIR
    from credit_monitor import check_budget_alert, record_usage
    import anthropic

    alert_level, _ = check_budget_alert()
    if alert_level == "danger":
        logger.warning("Budget exhausted — skipping rejection analysis.")
        return

    jobs = get_all_jobs()
    rejected = [j for j in jobs if (j.get("Status") or "").lower() == "rejected"]

    if len(rejected) < 5:
        logger.info("Rejection analysis: only %d rejections — need ≥5.", len(rejected))
        return

    # Build compact rejection summary
    lines = []
    for j in rejected:
        lines.append(
            f"Title: {j.get('Job Title','?')} | Company: {j.get('Company','?')} "
            f"| Location: {j.get('Location','?')} | Region: {j.get('Region','?')} "
            f"| Source: {j.get('Source','?')}"
        )
    rejection_text = "\n".join(lines[:40])  # cap at 40 rows

    prompt = (
        f"You are analyzing {len(rejected)} internship rejections for a "
        f"Data Science / ML candidate (EURECOM engineer, strong PyTorch / LLM fine-tuning / CV).\n\n"
        f"Rejection data:\n{rejection_text}\n\n"
        f"Analyze and answer:\n"
        f"1. PATTERNS: What company types, locations, or role titles reject most?\n"
        f"2. BLIND SPOTS: Is there a profile mismatch the candidate keeps targeting?\n"
        f"3. PIVOT: Which 2-3 specific adjustments would reduce rejections "
        f"   (different keywords, different company stage, different region, different role angle)?\n\n"
        f"Format as Telegram HTML (<b> for bold, - for bullets). "
        f"Be direct and data-driven. Under 300 words."
    )

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        record_usage(msg.usage.input_tokens, msg.usage.output_tokens, label="rejection_analysis")
        analysis = msg.content[0].text.strip()
    except Exception as exc:
        logger.error("Rejection analysis Claude call failed: %s", exc)
        return

    report = (
        f"📉 <b>Rejection Pattern Analysis</b>\n"
        f"<i>{len(rejected)} rejections analyzed</i>\n\n"
        f"{analysis}"
    )
    _send(report)
    logger.info("Rejection analysis sent to Telegram.")

    # Save for dashboard
    try:
        (DATA_DIR / "rejection_analysis.txt").write_text(analysis, encoding="utf-8")
        # Update last-run timestamp
        (DATA_DIR / "rejection_analysis_last_run.txt").write_text(
            datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"), encoding="utf-8"
        )
    except Exception as exc:
        logger.warning("Could not save rejection analysis: %s", exc)


def should_run_rejection_analysis() -> bool:
    """
    Returns True when rejections ≥ 5 and analysis hasn't run in the last 7 days.
    """
    from config import DATA_DIR
    jobs     = get_all_jobs()
    rejected = [j for j in jobs if (j.get("Status") or "").lower() == "rejected"]
    if len(rejected) < 5:
        return False

    last_run_file = DATA_DIR / "rejection_analysis_last_run.txt"
    if last_run_file.exists():
        try:
            last_run_str = last_run_file.read_text().strip()
            last_run     = datetime.strptime(last_run_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if (datetime.now(tz=timezone.utc) - last_run).days < 7:
                return False
        except Exception:
            pass

    return True


def should_run_weekly_analytics() -> bool:
    """
    Returns True on Sundays, once per day (end-of-week review).
    Uses a last-run file to prevent running twice on the same day.
    """
    from config import DATA_DIR

    today = datetime.now(tz=timezone.utc)
    if today.weekday() != 6:   # 6 = Sunday
        return False

    last_run_file = DATA_DIR / "analytics_last_run.txt"
    today_str = today.strftime("%Y-%m-%d")

    if last_run_file.exists():
        try:
            if last_run_file.read_text().strip() == today_str:
                return False
        except Exception:
            pass

    try:
        last_run_file.write_text(today_str)
    except Exception:
        pass

    return True


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
    generate_analytics_report()
    print("Analytics report sent to Telegram.")
