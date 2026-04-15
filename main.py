"""
Internship Finder — Main orchestrator.
Scrapes Indeed, LinkedIn, Glassdoor, and Wellfound via Apify.
Filters jobs by relevance using Claude before generating documents.
Generates custom resume + cover letter per compatible offer.
Updates Excel tracker and sends Telegram text report (no file attachments).

Usage:
    python3 main.py
"""

import logging
import os
import sys
from datetime import datetime, timezone

from config import DATA_DIR, MIN_RELEVANCE_SCORE, SEEN_IDS_PATH, TRACKER_PATH
from credit_monitor import check_budget_alert, get_today_summary
from doc_generator import generate_documents
from job_filter import filter_jobs
from notifier import (
    notify_budget_alert,
    notify_new_jobs,
    notify_run_complete,
    notify_single_job,
    _send,
)
from scraper import load_seen_ids, save_seen_ids, scrape_all
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

    # ── 1. Load seen job IDs (deduplication) ──────────────────────────────────
    seen_ids = load_seen_ids(SEEN_IDS_PATH)
    logger.info("Known job IDs: %d", len(seen_ids))

    # ── 2. Scrape new jobs ────────────────────────────────────────────────────
    try:
        all_scraped = scrape_all(seen_ids)
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

    if not compatible_jobs:
        logger.info("No compatible jobs after relevance filter.")
        # Persist all scraped IDs so we don't reprocess them
        seen_ids.update(j["job_id"] for j in all_scraped if j.get("job_id"))
        save_seen_ids(SEEN_IDS_PATH, seen_ids)
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
    same_hour = [
        j for j in compatible_jobs
        if j.get("posted_at") and j["posted_at"].hour == now_hour
    ]
    logger.info("Same-hour compatible offers: %d", len(same_hour))

    # ── 5. Generate documents per compatible job ──────────────────────────────
    rows = []
    for job in compatible_jobs:
        try:
            resume_path, cover_path = generate_documents(job)
            rows.append({**job, "resume_path": resume_path, "cover_path": cover_path})
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

    # ── 6. Update tracker ─────────────────────────────────────────────────────
    added = add_jobs(rows)
    logger.info("Tracker rows added: %d", added)

    # ── 7. Persist seen IDs (ALL scraped jobs, including rejected) ────────────
    seen_ids.update(j["job_id"] for j in all_scraped if j.get("job_id"))
    save_seen_ids(SEEN_IDS_PATH, seen_ids)

    # ── 8. Budget check ───────────────────────────────────────────────────────
    alert_level, alert_msg = check_budget_alert()
    if alert_level:
        notify_budget_alert(alert_msg, priority=5 if alert_level == "danger" else 4)
        if alert_level == "danger":
            logger.error("Budget exhausted — notifications only, no more generation.")

    # ── 9. Notifications (text only — no file attachments) ───────────────────
    # Same-hour priority alerts
    for job in same_hour[:3]:
        notify_single_job(job)

    # Full report of compatible offers
    notify_new_jobs(
        compatible_jobs,
        scraped_total=scraped_total,
        rejected_count=rejected_count,
    )

    # ── 10. Run-complete summary ──────────────────────────────────────────────
    cost_summary = get_today_summary()
    notify_run_complete(
        added,
        _count_tracker_rows(),
        cost_summary=cost_summary,
        scraped_total=scraped_total,
        rejected_count=rejected_count,
    )

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
