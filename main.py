"""
Internship Finder — Main orchestrator.
Scrapes Indeed, LinkedIn, Glassdoor, and Wellfound via Apify.
Filters jobs by relevance using Claude before generating documents.
Generates custom resume + cover letter per compatible offer.
Updates Excel tracker and sends Telegram report with PDF attachments.

Usage:
    python3 main.py
"""

import logging
import os
import sys
from datetime import datetime, timezone

from config import (
    DATA_DIR,
    MIN_RELEVANCE_SCORE,
    SEEN_FINGERPRINTS_PATH,
    SEEN_IDS_PATH,
    TRACKER_PATH,
)
from analytics import generate_analytics_report, should_run_weekly_analytics
from callback_handler import process_pending_callbacks
from dashboard_generator import generate_dashboard
from gmail_tracker import sync_gmail_statuses
from credit_monitor import check_budget_alert, get_apify_usage, get_today_summary
from doc_generator import generate_documents
from job_filter import filter_jobs
from notifier import (
    notify_budget_alert,
    notify_new_jobs,
    notify_run_complete,
    notify_single_job,
    send_documents,
    _send,
)
from outreach_generator import add_outreach_to_jobs
from scraper import (
    load_seen_fingerprints,
    load_seen_ids,
    save_seen_fingerprints,
    save_seen_ids,
    scrape_all,
    _make_fingerprint,
)
from skills_gap import analyze_skills_gap, save_scored_jobs, should_run_weekly_analysis
from tracker_manager import add_jobs

# ── Cloud detection ───────────────────────────────────────────────────────────
IS_CLOUD = os.getenv("GITHUB_ACTIONS") == "true"

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_PATH = DATA_DIR / "run.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(LOG_PATH), encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


def run():
    start = datetime.now(tz=timezone.utc)
    logger.info("=" * 60)
    logger.info("Internship Finder — run started at %s  [%s]",
                start.isoformat(), "CLOUD/GitHub Actions" if IS_CLOUD else "LOCAL/Mac")

    # ── 0. Process any pending Telegram callbacks (Applied button presses) ────
    # Flushes button taps that happened since the last run (when bot.py wasn't running)
    try:
        cb_count = process_pending_callbacks()
        if cb_count:
            logger.info("Flushed %d pending Telegram callback(s).", cb_count)
    except Exception as exc:
        logger.warning("Callback flush failed (non-critical): %s", exc)

    # ── 0b. Gmail sync — detect application responses ─────────────────────────
    try:
        import os
        if os.getenv("GMAIL_ENABLED", "").lower() == "true" or (BASE_DIR / "gmail_token.json").exists():
            sync_gmail_statuses(days_back=3)
    except Exception as exc:
        logger.warning("Gmail sync failed (non-critical): %s", exc)

    # ── 1. Load seen job IDs + content fingerprints (deduplication) ───────────
    seen_ids = load_seen_ids(SEEN_IDS_PATH)
    seen_fingerprints = load_seen_fingerprints(SEEN_FINGERPRINTS_PATH)
    logger.info("Known job IDs: %d  |  Known fingerprints: %d",
                len(seen_ids), len(seen_fingerprints))

    # ── 2. Scrape new jobs ────────────────────────────────────────────────────
    try:
        all_scraped = scrape_all(seen_ids, seen_fingerprints)
    except Exception as exc:
        logger.error("Scraping failed: %s", exc)
        notify_run_complete(0, 0, error=str(exc))
        return

    if not all_scraped:
        logger.info("No new jobs found — nothing to do.")
        notify_run_complete(0, _count_tracker_rows())
        return

    # ── 3. Relevance filter ───────────────────────────────────────────────────
    # Score every scraped job with Claude; keep only compatible ones.
    # Rejected jobs still get added to seen_ids so we don't re-score them.
    compatible_jobs, rejected_jobs = filter_jobs(all_scraped, MIN_RELEVANCE_SCORE)
    scraped_total  = len(all_scraped)
    rejected_count = len(rejected_jobs)

    # Persist ALL scored jobs for skills gap + analytics (regardless of compatibility)
    try:
        save_scored_jobs(compatible_jobs + rejected_jobs)
    except Exception as exc:
        logger.warning("save_scored_jobs failed (non-critical): %s", exc)

    if not compatible_jobs:
        logger.info("No compatible jobs after relevance filter.")
        # Persist scraped IDs (prevents re-scoring) but NOT fingerprints —
        # nothing was sent, so other sources should still be able to surface these jobs
        seen_ids.update(j["job_id"] for j in all_scraped if j.get("job_id"))
        save_seen_ids(SEEN_IDS_PATH, seen_ids)
        save_seen_fingerprints(SEEN_FINGERPRINTS_PATH, seen_fingerprints)
        notify_run_complete(
            0, _count_tracker_rows(),
            scraped_total=scraped_total,
            rejected_count=rejected_count,
        )
        return

    logger.info(
        "Compatible jobs: %d / %d scraped  (filtered out: %d)",
        len(compatible_jobs), scraped_total, rejected_count,
    )

    # ── 4. Identify same-hour jobs (highest priority) ─────────────────────────
    now_hour = datetime.now(tz=timezone.utc).hour
    same_hour_ids = {
        id(j) for j in compatible_jobs
        if j.get("posted_at") and j["posted_at"].hour == now_hour
    }
    same_hour = [j for j in compatible_jobs if id(j) in same_hour_ids]
    logger.info("Same-hour compatible offers: %d", len(same_hour))

    # ── 5. LinkedIn outreach drafts (score >= 8) ─────────────────────────────
    try:
        add_outreach_to_jobs(compatible_jobs)
    except Exception as exc:
        logger.warning("Outreach generation failed (non-critical): %s", exc)

    # ── 6. Generate documents + send PDFs per compatible job ─────────────────
    rows = []
    for job in compatible_jobs:
        try:
            resume_path, cover_path = generate_documents(job)
            rows.append({**job, "resume_path": resume_path, "cover_path": cover_path})
            send_documents(job, resume_path, cover_path)
        except RuntimeError as exc:
            # Budget exhausted mid-run — abort document generation
            logger.error("Budget stop: %s", exc)
            notify_budget_alert(str(exc), priority=5)
            rows.append({**job, "resume_path": "", "cover_path": ""})
            break
        except Exception as exc:
            logger.error("Doc generation failed for %s @ %s: %s",
                         job["title"], job["company"], exc)
            rows.append({**job, "resume_path": "", "cover_path": ""})

    # ── 7. Update tracker ─────────────────────────────────────────────────────
    added = add_jobs(rows)
    logger.info("Tracker rows added: %d", added)

    # ── 8. Persist seen IDs + fingerprints ───────────────────────────────────
    # seen_ids   → ALL scraped jobs (prevents re-scoring same job from same source)
    # fingerprints → ONLY compatible (sent) jobs (prevents duplicate Telegram alerts
    #                across sources; rejected jobs are NOT fingerprinted so a different
    #                source can surface them — this is what keeps Indeed/Glassdoor alive)
    seen_ids.update(j["job_id"] for j in all_scraped if j.get("job_id"))
    seen_fingerprints.update(
        _make_fingerprint(j.get("title", ""), j.get("company", ""))
        for j in compatible_jobs
    )
    save_seen_ids(SEEN_IDS_PATH, seen_ids)
    save_seen_fingerprints(SEEN_FINGERPRINTS_PATH, seen_fingerprints)

    # ── 9. Budget check ───────────────────────────────────────────────────────
    alert_level, alert_msg = check_budget_alert()
    if alert_level:
        notify_budget_alert(alert_msg, priority=5 if alert_level == "danger" else 4)
        if alert_level == "danger":
            logger.error("Budget exhausted — notifications only, no more generation.")

    # ── 10. Notifications (text only — no file attachments) ──────────────────
    # Same-hour priority alerts (up to 3)
    for job in same_hour[:3]:
        notify_single_job(job)

    # Full report — exclude same-hour jobs (already sent above as priority alerts)
    non_same_hour = [j for j in compatible_jobs if id(j) not in same_hour_ids]
    notify_new_jobs(
        non_same_hour,
        same_hour_jobs=same_hour,
        scraped_total=scraped_total,
        rejected_count=rejected_count,
    )

    # ── 11. Run-complete summary ──────────────────────────────────────────────
    cost_summary = get_today_summary()
    cost_summary["apify"] = get_apify_usage()
    notify_run_complete(
        added,
        _count_tracker_rows(),
        cost_summary=cost_summary,
        scraped_total=scraped_total,
        rejected_count=rejected_count,
    )

    # ── 12. Generate dashboard ───────────────────────────────────────────────
    try:
        generate_dashboard()
        logger.info("Dashboard updated.")
    except Exception as exc:
        logger.warning("Dashboard generation failed (non-critical): %s", exc)

    # ── 13. Weekly reports (skills gap on Mondays, analytics on Sundays) ─────
    try:
        if should_run_weekly_analysis():
            logger.info("Running weekly skills gap analysis (Monday trigger)...")
            analyze_skills_gap(days=7)
    except Exception as exc:
        logger.warning("Skills gap analysis failed (non-critical): %s", exc)

    try:
        if should_run_weekly_analytics():
            logger.info("Running weekly analytics report (Sunday trigger)...")
            generate_analytics_report()
    except Exception as exc:
        logger.warning("Analytics report failed (non-critical): %s", exc)

    elapsed = (datetime.now(tz=timezone.utc) - start).total_seconds()
    logger.info("Run finished in %.1fs — %d compatible jobs processed.", elapsed, len(compatible_jobs))
    logger.info("=" * 60)


def _count_tracker_rows() -> int:
    if not TRACKER_PATH.exists():
        return 0
    try:
        import openpyxl
        wb = openpyxl.load_workbook(str(TRACKER_PATH), read_only=True)
        ws = wb.active
        return max(0, ws.max_row - 1)
    except Exception:
        return 0


if __name__ == "__main__":
    run()
