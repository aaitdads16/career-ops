"""
followup_tracker.py — Smart follow-up cadence for applications.

How it works:
  - When a job is marked Applied, callback_handler records it in data/applications.json.
  - This module is called from followup-checker.yml (daily at 9 AM Paris) or main.py.
  - At day 7 with no Gmail response: Claude drafts a polite follow-up email.
  - At day 14 with no Gmail response: Claude drafts a final nudge.
  - Drafts are sent to Telegram. You copy-paste and send.
  - If the tracker shows Interview/Offer/Rejected, the application is skipped.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import anthropic

from config import ANTHROPIC_API_KEY, CANDIDATE, CLAUDE_MODEL, DATA_DIR
from credit_monitor import check_budget_alert, record_usage
from notifier import _send
from tracker_manager import get_all_jobs

logger = logging.getLogger(__name__)

APPS_FILE = DATA_DIR / "applications.json"

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


# ── Data helpers ──────────────────────────────────────────────────────────────

def _load_apps() -> list:
    if not APPS_FILE.exists():
        return []
    try:
        return json.loads(APPS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_apps(apps: list) -> None:
    APPS_FILE.write_text(json.dumps(apps, indent=2, ensure_ascii=False), encoding="utf-8")


def _sync_statuses_from_tracker(apps: list) -> list:
    """
    Pull latest statuses from tracker.xlsx so that Gmail-detected
    Interview/Offer/Rejected responses suppress follow-up emails.
    """
    tracker_map = {
        str(j.get("ID", "") or "").strip(): (j.get("Status") or "").strip()
        for j in get_all_jobs()
    }
    for app in apps:
        tracker_status = tracker_map.get(str(app.get("job_id", "")).strip())
        if tracker_status and tracker_status != app.get("status", ""):
            app["status"] = tracker_status
    return apps


# ── Email draft generation ────────────────────────────────────────────────────

def _generate_followup_email(
    company: str,
    title: str,
    days_since: int,
    is_final: bool,
) -> Optional[str]:
    """Call Claude to draft a follow-up email. Returns plain text or None."""
    alert_level, _ = check_budget_alert()
    if alert_level == "danger":
        logger.warning("Budget exhausted — skipping follow-up email generation.")
        return None

    tone = (
        "final, concise nudge (this is the second and last follow-up)"
        if is_final
        else "polite first follow-up, professional and brief"
    )

    prompt = f"""Write a {tone} email for a Data Science internship application.

Context:
- Candidate: {CANDIDATE['name']}
- Role applied for: {title} at {company}
- Days since application: {days_since}
- Email: {CANDIDATE['email']}
- LinkedIn: {CANDIDATE['linkedin']}

Rules:
- Subject line on the first line, prefixed with "Subject: "
- Under 120 words total (body only)
- Professional but warm — no desperation, no clichés like "I am very excited"
- Mention ONE specific thing about the role/company (infer from company name + role title)
- End with availability for a call + contact info
- No placeholders — write it ready to send
- Plain text only, no markdown

Output format:
Subject: [subject]

[email body]
"""

    try:
        msg = _get_client().messages.create(
            model=CLAUDE_MODEL,
            max_tokens=350,
            messages=[{"role": "user", "content": prompt}],
        )
        record_usage(msg.usage.input_tokens, msg.usage.output_tokens, label="followup")
        return msg.content[0].text.strip()
    except Exception as exc:
        logger.error("Follow-up email generation failed: %s", exc)
        return None


# ── Main runner ───────────────────────────────────────────────────────────────

def check_and_send_followups() -> int:
    """
    Check all applied jobs for follow-up triggers and send Telegram drafts.
    Returns number of follow-ups sent.
    """
    apps = _load_apps()
    if not apps:
        logger.info("No applications in applications.json — skipping follow-ups.")
        return 0

    apps = _sync_statuses_from_tracker(apps)

    now   = datetime.now(tz=timezone.utc)
    sent  = 0

    for app in apps:
        status = app.get("status", "Applied")
        # Skip if already has a response
        if status in ("Interview", "Offer", "Rejected"):
            continue

        applied_at_str = app.get("applied_at", "")
        if not applied_at_str:
            continue

        try:
            applied_at = datetime.fromisoformat(applied_at_str)
            if applied_at.tzinfo is None:
                applied_at = applied_at.replace(tzinfo=timezone.utc)
            days_since = (now - applied_at).days
        except Exception:
            continue

        company   = app.get("company", "the company")
        title     = app.get("title", "the role")
        job_id    = app.get("job_id", "")
        f7_sent   = app.get("followup_7_sent", False)
        f14_sent  = app.get("followup_14_sent", False)

        trigger_7  = days_since >= 7  and not f7_sent
        trigger_14 = days_since >= 14 and not f14_sent

        if trigger_14:
            draft = _generate_followup_email(company, title, days_since, is_final=True)
            if draft:
                _send(
                    f"📬 <b>Final Follow-up Draft</b> ({days_since} days)\n"
                    f"<b>{title}</b> @ {company}\n"
                    f"<code>{job_id}</code>\n\n"
                    f"<pre>{draft[:900]}</pre>"
                )
                app["followup_14_sent"] = True
                sent += 1
        elif trigger_7:
            draft = _generate_followup_email(company, title, days_since, is_final=False)
            if draft:
                _send(
                    f"📬 <b>Follow-up Draft</b> ({days_since} days)\n"
                    f"<b>{title}</b> @ {company}\n"
                    f"<code>{job_id}</code>\n\n"
                    f"<pre>{draft[:900]}</pre>"
                )
                app["followup_7_sent"] = True
                sent += 1

    _save_apps(apps)
    logger.info("Follow-up check complete: %d draft(s) sent.", sent)
    return sent


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
    n = check_and_send_followups()
    print(f"✓ {n} follow-up(s) sent.")
