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
from datetime import datetime, timezone

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
