"""
gmail_tracker.py — Gmail integration for automatic application status tracking.

Improvements over v1:
  - ATS domain detection (Greenhouse, Lever, Workday, Ashby, etc.)
  - Company name matched in subject + body, not just sender domain
  - Broader search query to catch confirmation emails from ATS systems
  - Full email body fetched when snippet doesn't match
  - /gmailsync [days] command support via sync_gmail_statuses(days_back=N)
"""

import base64
import json
import logging
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import DATA_DIR
from notifier import _send
from tracker_manager import get_all_jobs

logger = logging.getLogger(__name__)

GMAIL_SYNC_FILE = DATA_DIR / "gmail_last_sync.txt"
GMAIL_SEEN_FILE = DATA_DIR / "gmail_seen_ids.txt"

# ── Known ATS/HR platform sender domains ─────────────────────────────────────
# Emails from these domains carry the company name in the subject, not the sender.
_ATS_DOMAINS = {
    "greenhouse.io", "greenhouse-mail.io",
    "lever.co", "jobs.lever.co",
    "workday.com", "myworkday.com",
    "taleo.net", "oraclecloud.com",
    "icims.com",
    "jobvite.com",
    "smartrecruiters.com",
    "successfactors.com", "sapsf.com",
    "bamboohr.com",
    "recruitee.com",
    "teamtailor.com",
    "workable.com",
    "ashbyhq.com", "ashby.io",
    "pinpointhq.com",
    "applytojob.com",
    "rippling.com",
    "personio.de", "personio.com",
    "join.com",
    "welcometothejungle.com", "wttj.co",
    "talent.io",
    "breezy.hr",
    "jazz.co", "jazzhr.com",
    "lever.email",
    "comeet.com",
    "hi.hr",
    "bullhornstaffing.com",
    "careers-page.com",
    "linkedin.com", "mail.linkedin.com",
    "indeed.com", "indeedmail.com",
    "glassdoor.com",
    "angel.co", "wellfound.com",
}

# ── Status classification ─────────────────────────────────────────────────────

_STATUS_PATTERNS = {
    "Offer": [
        r"\boffer\b", r"\bcongratulations\b", r"\bcongrats\b",
        r"pleased to (offer|extend|inform)", r"welcome to the team",
        r"we('d| would) like to offer", r"formal offer",
        r"offer letter", r"compensation package",
    ],
    "Interview": [
        r"\binterview\b", r"\btechnical (screen|call|round|test|interview)\b",
        r"schedule.*call", r"next (round|step|stage)",
        r"we('d| would) like to (invite|schedule|arrange)",
        r"coding (challenge|test|assessment)", r"take.*home.*test",
        r"take-home", r"technical assessment", r"technical exercise",
        r"hiring manager", r"\bvideo call\b", r"\bphone screen\b",
        r"\bphone call\b", r"let's (chat|talk|connect)",
        r"30.minute", r"45.minute", r"60.minute",
        r"calendly", r"schedule.*time", r"book.*time",
        r"move.*forward", r"advance.*process",
        r"shortlisted", r"short.listed",
    ],
    "Rejected": [
        r"\bunfortunately\b", r"regret to inform",
        r"not (moving|proceeding|continuing|selected|advancing)",
        r"other candidates", r"decided (not to|to move)",
        r"position has been filled", r"no longer (considering|moving)",
        r"will not be (moving|proceeding)", r"wish you (all the|the very) best",
        r"keep your (resume|cv|profile) on file",
        r"highly competitive", r"not.*right fit",
        r"gone with (another|a different)", r"filled the (role|position)",
        r"not selected", r"not been successful",
        r"after careful (review|consideration)",
        r"we have decided", r"we('ve| have) decided",
    ],
    "Applied": [
        r"(received|confirmed|submitted).*application",
        r"application.*received",
        r"thank you for (applying|your application|your interest)",
        r"we('ve| have) received your",
        r"application.*under review",
        r"application.*submitted",
        r"successfully (applied|submitted)",
        r"your (application|candidacy|profile).*been (received|submitted|sent)",
        r"application confirmation",
        r"we'll (review|be in touch)",
        r"our team will review",
        r"one of our recruiters",
        r"will be in touch",
        r"application.*complete",
        r"you applied",
    ],
}


def _classify_email(subject: str, body: str) -> str:
    """Return best status match (Offer > Interview > Rejected > Applied) or ''."""
    text = (subject + " " + body).lower()
    for status in ("Offer", "Interview", "Rejected", "Applied"):
        for pattern in _STATUS_PATTERNS[status]:
            if re.search(pattern, text):
                return status
    return ""


def _is_ats_sender(sender: str) -> bool:
    sender_lower = sender.lower()
    return any(domain in sender_lower for domain in _ATS_DOMAINS)


def _extract_company_from_subject(subject: str, companies: set) -> str:
    """
    Try to find a tracked company name in the email subject.
    Works for ATS patterns like:
      "Your application at Google",  "Data Intern - Meta | Greenhouse",
      "Thank you for applying to Airbnb", "[Lever] Applied to Netflix"
    """
    subj_lower = subject.lower()

    # Direct company name match (most reliable)
    for co in companies:
        if len(co) >= 3 and co in subj_lower:
            return co

    # Pattern-based extraction: "at/to/from/with COMPANY" — then match against tracker
    patterns = [
        r"\bat\s+([A-Za-z0-9][A-Za-z0-9\s&\.\-]{2,40}?)(?:\s*[-|,\(]|$)",
        r"\bto\s+([A-Za-z0-9][A-Za-z0-9\s&\.\-]{2,40}?)(?:\s*[-|,\(]|$)",
        r"\bfrom\s+([A-Za-z0-9][A-Za-z0-9\s&\.\-]{2,40}?)(?:\s*[-|,\(]|$)",
        r"-\s*([A-Za-z0-9][A-Za-z0-9\s&\.\-]{2,40}?)(?:\s*[-|,\(]|$)",
        r"\|\s*([A-Za-z0-9][A-Za-z0-9\s&\.\-]{2,40}?)(?:\s*[-|,\(]|$)",
    ]
    for pat in patterns:
        m = re.search(pat, subject, re.IGNORECASE)
        if m:
            candidate = m.group(1).strip().lower()
            # Check if any tracked company is a substring of the candidate or vice versa
            for co in companies:
                if len(co) >= 3 and (co in candidate or candidate in co):
                    return co
    return ""


# ── Gmail API helpers ─────────────────────────────────────────────────────────

def _build_service():
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
    token_file = Path(__file__).parent

    if token_json:
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
        token_path = token_file / "gmail_token.json"
        if not token_path.exists():
            logger.warning("Gmail not configured — run 'python3 gmail_setup.py' to set up OAuth.")
            return None
        creds = Credentials.from_authorized_user_file(str(token_path))

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


def _get_email_body(service, msg_id: str) -> str:
    """Fetch the plain-text body of an email. Returns empty string on failure."""
    try:
        detail = (
            service.users().messages()
            .get(userId="me", id=msg_id, format="full")
            .execute()
        )
        payload = detail.get("payload", {})

        def extract_text(part):
            mime = part.get("mimeType", "")
            if mime == "text/plain":
                data = (part.get("body") or {}).get("data", "")
                if data:
                    try:
                        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
                    except Exception:
                        pass
            for sub in part.get("parts", []):
                result = extract_text(sub)
                if result:
                    return result
            return ""

        return extract_text(payload)[:3000]
    except Exception:
        return ""


# ── Main sync function ────────────────────────────────────────────────────────

def sync_gmail_statuses(days_back: int = 30) -> list:
    """
    Search Gmail for application-related emails from the past `days_back` days.
    Update tracker for any status changes found.
    Returns list of changes: [{company, title, old_status, new_status, subject}]
    """
    service = _build_service()
    if service is None:
        return []

    tracker_jobs = get_all_jobs()
    if not tracker_jobs:
        logger.info("Gmail sync: tracker empty, nothing to match.")
        return []

    # Build lookup: company_name_lower → tracker_row
    job_by_company = {}
    for j in tracker_jobs:
        co = (j.get("Company") or "").lower().strip()
        if co and co not in ("-", "–"):
            job_by_company[co] = j

    companies_lower = set(job_by_company.keys())

    seen_ids = _load_seen_ids()
    changes  = []

    # Broad Gmail query — catches confirmation, rejection, interview, and ATS emails
    since_ts = int((datetime.now(tz=timezone.utc) - timedelta(days=days_back)).timestamp())
    query = (
        f"after:{since_ts} "
        "(subject:(application OR interview OR offer OR assessment OR "
        "\"thank you for applying\" OR \"thank you for your application\" OR "
        "\"unfortunately\" OR \"next steps\" OR \"move forward\" OR "
        "\"we received\" OR \"application received\" OR \"application confirmation\" OR "
        "\"invitation to\" OR \"coding challenge\" OR \"technical\" OR "
        "\"your candidacy\" OR \"your profile\") "
        "OR from:(greenhouse.io OR lever.co OR workday.com OR ashbyhq.com OR "
        "icims.com OR jobvite.com OR smartrecruiters.com OR teamtailor.com OR "
        "workable.com OR bamboohr.com OR recruitee.com OR taleo.net))"
    )

    try:
        result = (
            service.users().messages()
            .list(userId="me", q=query, maxResults=200)
            .execute()
        )
        messages = result.get("messages", [])
        # Handle pagination for large mailboxes
        while result.get("nextPageToken") and len(messages) < 500:
            result = (
                service.users().messages()
                .list(userId="me", q=query, maxResults=200,
                      pageToken=result["nextPageToken"])
                .execute()
            )
            messages.extend(result.get("messages", []))
    except Exception as exc:
        logger.error("Gmail list failed: %s", exc)
        return []

    logger.info("Gmail sync: %d candidate emails to check (last %d days).", len(messages), days_back)

    for msg in messages:
        msg_id = msg["id"]
        if msg_id in seen_ids:
            continue

        try:
            meta = (
                service.users().messages()
                .get(userId="me", id=msg_id, format="metadata",
                     metadataHeaders=["Subject", "From", "Date"])
                .execute()
            )
        except Exception as exc:
            logger.warning("Gmail get message failed (%s): %s", msg_id, exc)
            continue

        seen_ids.add(msg_id)

        headers = {h["name"]: h["value"] for h in meta.get("payload", {}).get("headers", [])}
        subject = headers.get("Subject", "")
        sender  = headers.get("From", "").lower()
        snippet = meta.get("snippet", "")

        # ── Step 1: Identify which company this email is about ───────────────
        matched_job = None
        is_ats = _is_ats_sender(sender)

        if is_ats:
            # ATS email: company name is in the subject
            co_key = _extract_company_from_subject(subject, companies_lower)
            if co_key:
                matched_job = job_by_company.get(co_key)

        if not matched_job:
            # Try direct sender domain match
            for co_key, job in job_by_company.items():
                co_words = [w for w in co_key.split() if len(w) >= 4]
                if co_words and any(w in sender for w in co_words):
                    matched_job = job
                    break

        if not matched_job:
            # Try matching company name in subject line
            co_key = _extract_company_from_subject(subject, companies_lower)
            if co_key:
                matched_job = job_by_company.get(co_key)

        if not matched_job:
            # Try matching company name in snippet
            for co_key, job in job_by_company.items():
                if len(co_key) >= 4 and co_key in snippet.lower():
                    matched_job = job
                    break

        if not matched_job:
            # Last resort: fetch full body and scan for company names
            body = _get_email_body(service, msg_id)
            if body:
                body_lower = body.lower()
                for co_key, job in job_by_company.items():
                    if len(co_key) >= 4 and co_key in body_lower:
                        matched_job = job
                        break
                if not matched_job:
                    continue
                # Use body for classification too
                snippet = body[:500]
            else:
                continue

        # ── Step 2: Classify the email ───────────────────────────────────────
        new_status = _classify_email(subject, snippet)
        if not new_status:
            continue

        old_status = matched_job.get("Status") or "Waiting to apply"
        job_id     = str(matched_job.get("ID") or "")

        # Only upgrade status (Waiting→Applied→Interview/Rejected→Offer)
        status_rank = {"Waiting to apply": 0, "Applied": 1,
                       "Interview": 2, "Rejected": 2, "Offer": 3}
        if status_rank.get(new_status, 0) <= status_rank.get(old_status, 0):
            continue

        # ── Step 3: Update tracker ───────────────────────────────────────────
        from tracker_manager import (
            _get_or_create_wb, HEADERS, _color_status_cell, TRACKER_PATH
        )
        try:
            wb, ws = _get_or_create_wb()
            id_col     = HEADERS.index("ID") + 1
            status_col = HEADERS.index("Status") + 1
            notes_col  = HEADERS.index("Notes") + 1
            for row in ws.iter_rows(min_row=2):
                if str(row[id_col - 1].value or "").strip() == job_id:
                    row[status_col - 1].value = new_status
                    _color_status_cell(row[status_col - 1], new_status)
                    note = f"Gmail auto: '{subject[:60]}'"
                    existing = str(row[notes_col - 1].value or "")
                    row[notes_col - 1].value = (
                        f"{existing}  [{note}]".strip() if existing else note
                    )
                    break
            wb.save(str(TRACKER_PATH))
        except Exception as exc:
            logger.error("Tracker update failed for %s: %s",
                         matched_job.get("Company"), exc)
            continue

        # Also write to statuses.json for live dashboard overlay
        try:
            from callback_handler import _write_status_override
            _write_status_override(job_id, new_status)
        except Exception:
            pass

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

    try:
        now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        GMAIL_SYNC_FILE.write_text(now_str)
    except Exception:
        pass

    if changes:
        status_emoji = {"Applied": "✅", "Interview": "🎯", "Offer": "🎉", "Rejected": "❌"}
        lines = [f"📬 <b>Gmail auto-update — {len(changes)} status change(s)</b>\n"]
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
        logger.info("Gmail sync complete: no new status changes.")

    return changes
