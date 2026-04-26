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
from pathlib import Path

from config import (
    BASE_DIR,
    DATA_DIR,
    MIN_RELEVANCE_SCORE,
    SEEN_FINGERPRINTS_PATH,
    SEEN_IDS_PATH,
    TRACKER_PATH,
)
from analytics import (
    analyze_rejections,
    generate_analytics_report,
    should_run_rejection_analysis,
    should_run_weekly_analytics,
)
from callback_handler import process_pending_callbacks
from dashboard_generator import generate_dashboard
from followup_tracker import check_and_send_followups
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
from jd_archive import archive_jobs
from tracker_manager import add_jobs, apply_status_overrides

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


def _is_paused() -> bool:
    """Return True if a pause_until.txt file exists and is still in the future."""
    pause_file = DATA_DIR / "pause_until.txt"
    if not pause_file.exists():
        return False
    try:
        from datetime import datetime, timezone
        until = datetime.fromisoformat(pause_file.read_text().strip())
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        if datetime.now(tz=timezone.utc) < until:
            logger.info("⏸️ Scraping paused until %s — skipping run.", until.isoformat())
            return True
        else:
            pause_file.unlink()   # expired — remove the file
    except Exception:
        pass
    return False


def _consume_search_trigger() -> list:
    """
    Check if a /search command queued a custom keyword.
    Returns list of extra keywords to add to this run (empty if none).
    """
    trigger_file = DATA_DIR / "search_trigger.json"
    if not trigger_file.exists():
        return []
    try:
        import json
        data = json.loads(trigger_file.read_text())
        keyword = data.get("keyword", "").strip()
        if keyword:
            trigger_file.unlink()   # consume it
            logger.info("Custom search trigger: '%s'", keyword)
            return [keyword]
    except Exception:
        pass
    return []


def run():
    start = datetime.now(tz=timezone.utc)
    logger.info("=" * 60)
    logger.info("Internship Finder — run started at %s  [%s]",
                start.isoformat(), "CLOUD/GitHub Actions" if IS_CLOUD else "LOCAL/Mac")

    # ── 0. Apply any dashboard status overrides (written directly from the web UI) ─
    try:
        overrides_applied = apply_status_overrides()
        if overrides_applied:
            logger.info("Dashboard overrides applied: %d row(s) updated.", overrides_applied)
    except Exception as exc:
        logger.warning("apply_status_overrides failed (non-critical): %s", exc)

    # ── 0. Process any pending Telegram callbacks + commands ─────────────────
    try:
        cb_count = process_pending_callbacks()
        if cb_count:
            logger.info("Flushed %d pending Telegram update(s).", cb_count)
    except Exception as exc:
        logger.warning("Callback flush failed (non-critical): %s", exc)

    # ── 0c. Check for pause ───────────────────────────────────────────────────
    if _is_paused():
        notify_run_complete(0, _count_tracker_rows())
        return

    # ── 0b. Gmail sync — detect application responses ─────────────────────────
    try:
        import os
        if os.getenv("GMAIL_ENABLED", "").lower() == "true" or (BASE_DIR / "gmail_token.json").exists():
            sync_gmail_statuses(days_back=30)
    except Exception as exc:
        logger.warning("Gmail sync failed (non-critical): %s", exc)

    # ── 0d. Consume custom /search trigger (queued via Telegram command) ──────
    extra_keywords = _consume_search_trigger()

    # ── 1. Load seen job IDs + content fingerprints (deduplication) ───────────
    seen_ids = load_seen_ids(SEEN_IDS_PATH)
    seen_fingerprints = load_seen_fingerprints(SEEN_FINGERPRINTS_PATH)
    logger.info("Known job IDs: %d  |  Known fingerprints: %d",
                len(seen_ids), len(seen_fingerprints))

    # ── 2. Scrape new jobs ────────────────────────────────────────────────────
    # Pass extra_keywords from /search command to scraper
    try:
        all_scraped = scrape_all(seen_ids, seen_fingerprints, extra_keywords=extra_keywords)
    except RuntimeError as exc:
        exc_str = str(exc)
        logger.error("Scraping aborted: %s", exc_str)
        if "quota" in exc_str.lower() or "apify" in exc_str.lower():
            notify_budget_alert(
                f"🚫 *Apify quota exhausted* — all scrapers returned 0 jobs.\n"
                f"Top up at [console.apify.com/billing](https://console.apify.com/billing)\n\n"
                f"_{exc_str}_",
                priority=5,
            )
        notify_run_complete(0, _count_tracker_rows(), error=exc_str)
        return
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

    # ── 3c. Within-run deduplication (same job from 2 scrapers in same run) ───
    _seen_this_run: set = set()
    _deduped: list = []
    for _j in compatible_jobs:
        _fp = _make_fingerprint(_j.get("title", ""), _j.get("company", ""))
        if _fp not in seen_fingerprints and _fp not in _seen_this_run:
            _deduped.append(_j)
            _seen_this_run.add(_fp)
        else:
            logger.info("Within-run duplicate dropped: %s @ %s",
                        _j.get("title"), _j.get("company"))
    if len(_deduped) < len(compatible_jobs):
        logger.info("Within-run dedup: %d → %d jobs", len(compatible_jobs), len(_deduped))
    compatible_jobs = _deduped

    logger.info(
        "Compatible jobs: %d / %d scraped  (filtered out: %d)",
        len(compatible_jobs), scraped_total, rejected_count,
    )

    # ── 3b. Archive JD text so /regenerate and skills-gap always have it ──────
    try:
        saved = archive_jobs(compatible_jobs)
        logger.info("JD archive: %d description(s) saved.", saved)
    except Exception as exc:
        logger.warning("JD archiving failed (non-critical): %s", exc)

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

    # ── 7b. Copy PDFs to docs/pdfs/ for dashboard download links ─────────────
    try:
        import shutil as _shutil
        _pdfs_resumes = BASE_DIR / "docs" / "pdfs" / "resumes"
        _pdfs_covers  = BASE_DIR / "docs" / "pdfs" / "covers"
        _pdfs_resumes.mkdir(parents=True, exist_ok=True)
        _pdfs_covers.mkdir(parents=True, exist_ok=True)
        for row in rows:
            for src_key, dst_dir in [("resume_path", _pdfs_resumes), ("cover_path", _pdfs_covers)]:
                src = str(row.get(src_key, ""))
                if src and Path(src).exists():
                    _shutil.copy2(src, dst_dir / Path(src).name)
        logger.info("PDFs copied to docs/pdfs/ for dashboard download links.")
    except Exception as exc:
        logger.warning("PDF copy to docs/pdfs/ failed (non-critical): %s", exc)

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

    # ── 14. Rejection pattern analysis (auto-triggered at ≥5 rejections) ─────
    try:
        if should_run_rejection_analysis():
            logger.info("Running rejection pattern analysis...")
            analyze_rejections()
    except Exception as exc:
        logger.warning("Rejection analysis failed (non-critical): %s", exc)

    # ── 15. Follow-up check (daily at 9 AM via workflow, but also runs here) ──
    try:
        check_and_send_followups()
    except Exception as exc:
        logger.warning("Follow-up check failed (non-critical): %s", exc)

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
