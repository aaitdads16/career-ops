"""
gmail_tracker.py — Gmail integration for automatic application status tracking.

How it works:
  1. Searches Gmail for emails from companies in the tracker
  2. Classifies email intent (received / interview / offer / rejected)
  3. Updates tracker status automatically
  4. Sends a Telegram summary of status changes

Setup (one-time, local):
  python3 gmail_setup.py
  → Opens browser for OAuth → saves credentials
  → Copy the printed JSON to GitHub Secret GMAIL_TOKEN_JSON

Then it runs automatically as part of internship-finder.yml.
"""

import json
import logging
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import DATA_DIR
from notifier import _send
from tracker_manager import get_all_jobs, mark_applied

logger = logging.getLogger(__name__)

GMAIL_SYNC_FILE = DATA_DIR / "gmail_last_sync.txt"
GMAIL_SEEN_FILE = DATA_DIR / "gmail_seen_ids.txt"


# ── Status classification ─────────────────────────────────────────────────────

_STATUS_PATTERNS = {
    "Offer": [
        r"\boffer\b", r"\bcongratulations\b", r"\bcongrats\b",
        r"pleased to (offer|extend|inform)", r"welcome to the team",
        r"we('d| would) like to offer", r"formal offer",
    ],
    "Interview": [
        r"\binterview\b", r"\btechnical (screen|call|round|test)\b",
        r"schedule.*call", r"next (round|step|stage)",
        r"we('d| would) like to (invite|schedule|arrange)",
        r"coding (challenge|test|assessment)", r"take.*home.*test",
        r"hiring manager", r"\bvideo call\b", r"\bphone screen\b",
    ],
    "Rejected": [
        r"\bunfortunately\b", r"regret to inform",
        r"not (moving|proceeding|continuing|selected|advancing)",
        r"other candidates", r"decided (not to|to move)",
        r"position has been filled", r"no longer (considering|moving)",
        r"will not be (moving|proceeding)", r"wish you (all the|the very) best",
        r"keep your (resume|cv|profile) on file",
    ],
    "Applied": [
        r"(received|confirmed|submitted).*application",
        r"application.*received",
        r"thank you for (applying|your application|your interest)",
        r"we('ve| have) received your",
        r"application.*under review",
    ],
}

def _classify_email(subject: str, snippet: str) -> str:
    """
    Return the best status match for an email, or '' if unrecognised.
    Priority: Offer > Interview > Rejected > Applied
    """
    text = (subject + " " + snippet).lower()
    for status in ("Offer", "Interview", "Rejected", "Applied"):
        for pattern in _STATUS_PATTERNS[status]:
            if re.search(pattern, text):
                return status
    return ""


# ── Gmail API helpers ─────────────────────────────────────────────────────────

def _build_service():
    """Build an authenticated Gmail service object."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        logger.error(
            "Google API libraries not installed. "
            "Run: pip install google-auth-httplib2 google-auth-oauthlib google-api-python-client"
        )
        return None

    token_json = os.getenv("GMAIL_TOKEN_JSON", "")
    token_file = BASE_DIR = Path(__file__).parent

    if token_json:
        # GitHub Actions: read from env var
        try:
            creds_data = json.loads(token_json)
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, encoding="utf-8"
            ) as tf:
                json.dump(creds_data, tf)
                tmp_path = tf.name
            creds = Credentials.from_authorized_user_file(tmp_path)
            Path(tmp_path).unlink(missing_ok=True)
        except Exception as exc:
            logger.error("Failed to load GMAIL_TOKEN_JSON: %s", exc)
            return None
    else:
        # Local: read from file
        token_path = token_file / "gmail_token.json"
        if not token_path.exists():
            logger.warning(
                "Gmail not configured — run 'python3 gmail_setup.py' to set up OAuth."
            )
            return None
        creds = Credentials.from_authorized_user_file(str(token_path))

    # Refresh if expired
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as exc:
            logger.error("Gmail token refresh failed: %s", exc)
            return None

    try:
        return build("gmail", "v1", credentials=creds, cache_discovery=False)
    except Exception as exc:
        logger.error("Gmail service build failed: %s", exc)
        return None


def _load_seen_ids() -> set:
    try:
        if GMAIL_SEEN_FILE.exists():
            return set(GMAIL_SEEN_FILE.read_text().splitlines())
    except Exception:
        pass
    return set()


def _save_seen_ids(ids: set) -> None:
    try:
        GMAIL_SEEN_FILE.write_text("\n".join(sorted(ids)))
    except Exception as exc:
        logger.warning("Could not save gmail seen IDs: %s", exc)


# ── Main sync function ────────────────────────────────────────────────────────

def sync_gmail_statuses(days_back: int = 7) -> list[dict]:
    """
    Search Gmail for application-related emails from the past `days_back` days.
    Update tracker for any status changes found.
    Returns list of changes: [{company, title, old_status, new_status, subject}]

    Called from main.py before each run.
    """
    service = _build_service()
    if service is None:
        return []

    # Build a set of (company, job_id) pairs from the tracker
    tracker_jobs = get_all_jobs()
    if not tracker_jobs:
        logger.info("Gmail sync: tracker empty, nothing to match.")
        return []

    companies = {
        (j.get("Company") or "").lower().strip()
        for j in tracker_jobs
        if j.get("Company")
    }
    job_by_company = {}
    for j in tracker_jobs:
        co = (j.get("Company") or "").lower().strip()
        if co:
            job_by_company[co] = j

    seen_ids = _load_seen_ids()
    changes  = []

    # Date filter for Gmail query
    since_ts  = int((datetime.now(tz=timezone.utc) - timedelta(days=days_back)).timestamp())
    # Build Gmail query: look for application-related subjects
    query = (
        f"after:{since_ts} "
        "subject:(application OR interview OR offer OR assessment OR "
        "\"thank you for applying\" OR \"unfortunately\" OR \"next steps\")"
    )

    try:
        result = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=100)
            .execute()
        )
        messages = result.get("messages", [])
    except Exception as exc:
        logger.error("Gmail list failed: %s", exc)
        return []

    logger.info("Gmail sync: %d candidate emails to check.", len(messages))

    for msg in messages:
        msg_id = msg["id"]
        if msg_id in seen_ids:
            continue

        try:
            detail = (
                service.users()
                .messages()
                .get(userId="me", id=msg_id, format="metadata",
                     metadataHeaders=["Subject", "From", "Date"])
                .execute()
            )
        except Exception as exc:
            logger.warning("Gmail get message failed (%s): %s", msg_id, exc)
            continue

        seen_ids.add(msg_id)

        headers  = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
        subject  = headers.get("Subject", "")
        sender   = headers.get("From", "").lower()
        snippet  = detail.get("snippet", "")

        # Match sender to a company in the tracker
        matched_co = None
        matched_job = None
        for co_key, job in job_by_company.items():
            # Check if company name appears in sender domain or snippet
            co_words = [w for w in co_key.split() if len(w) > 3]
            if any(w in sender for w in co_words):
                matched_co  = co_key
                matched_job = job
                break
            if any(w in snippet.lower() for w in co_words):
                matched_co  = co_key
                matched_job = job
                break

        if not matched_job:
            continue

        new_status = _classify_email(subject, snippet)
        if not new_status:
            continue

        old_status = matched_job.get("Status") or "Waiting to apply"
        job_id     = str(matched_job.get("ID") or "")

        # Only upgrade status (don't downgrade Interview → Applied etc.)
        status_rank = {"Waiting to apply": 0, "Applied": 1, "Interview": 2, "Rejected": 2, "Offer": 3}
        if status_rank.get(new_status, 0) <= status_rank.get(old_status, 0):
            continue

        # Update tracker
        from tracker_manager import _get_or_create_wb, HEADERS, _color_status_cell, TRACKER_PATH
        import openpyxl
        try:
            wb, ws = _get_or_create_wb()
            id_col     = HEADERS.index("ID") + 1
            status_col = HEADERS.index("Status") + 1
            notes_col  = HEADERS.index("Notes") + 1
            for row in ws.iter_rows(min_row=2):
                if str(row[id_col - 1].value or "").strip() == job_id:
                    row[status_col - 1].value = new_status
                    _color_status_cell(row[status_col - 1], new_status)
                    note = f"Auto-detected via Gmail: '{subject[:60]}'"
                    existing = str(row[notes_col - 1].value or "")
                    row[notes_col - 1].value = f"{existing}  [{note}]".strip() if existing else note
                    break
            wb.save(str(TRACKER_PATH))
        except Exception as exc:
            logger.error("Tracker update failed for %s: %s", matched_job.get("Company"), exc)
            continue

        changes.append({
            "company":    matched_job.get("Company", ""),
            "title":      matched_job.get("Job Title", ""),
            "old_status": old_status,
            "new_status": new_status,
            "subject":    subject,
        })
        logger.info(
            "Gmail: %s @ %s  %s → %s  (%s)",
            matched_job.get("Job Title"), matched_job.get("Company"),
            old_status, new_status, subject[:60],
        )

    _save_seen_ids(seen_ids)

    # Record sync time
    try:
        now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        GMAIL_SYNC_FILE.write_text(now_str)
    except Exception:
        pass

    # Telegram notification
    if changes:
        lines = ["📬 <b>Gmail auto-update — application statuses changed</b>\n"]
        status_emoji = {"Applied": "✅", "Interview": "🎯", "Offer": "🎉", "Rejected": "❌"}
        for c in changes:
            emoji = status_emoji.get(c["new_status"], "🔄")
            lines.append(
                f"{emoji} <b>{c['title']}</b> @ {c['company']}\n"
                f"   {c['old_status']} → <b>{c['new_status']}</b>\n"
                f"   📧 {c['subject'][:70]}"
            )
        _send("\n\n".join(lines))
        logger.info("Gmail sync complete: %d status change(s).", len(changes))
    else:
        logger.info("Gmail sync complete: no status changes.")

    return changes
