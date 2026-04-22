"""
callback_handler.py — Telegram callback queries + bot commands.

Two usage modes:
  1. One-shot flush (called at the start of every main.py run):
       from callback_handler import process_pending_callbacks
       process_pending_callbacks()

  2. Persistent real-time polling (run bot.py locally):
       python3 bot.py

── Callbacks ────────────────────────────────────────────────────────────────
  applied:{job_id}     → marks job Applied in tracker; deferred if not found yet

── Bug: timing race fix ─────────────────────────────────────────────────────
  The main workflow sends Telegram messages BEFORE committing tracker.xlsx.
  If the user taps "✅ Applied" within the same 15-min window, the callback
  workflow can't find the job.  Fix: save to data/pending_apply.json and retry
  on every subsequent run until the job appears in the tracker (or 10 retries).

── Bot Commands ─────────────────────────────────────────────────────────────
  /help                       → command list
  /status                     → tracker summary
  /stats                      → full analytics funnel
  /budget                     → Anthropic + Apify credit balance
  /pause [Nh | Nd]            → pause scraping (default 24h, max 168h)
  /resume                     → cancel pause
  /search [keyword]           → queue a targeted search for the next main run
  /followup                   → show applications needing follow-up (≥7 days)
  /setstatus [job_id] [status] → update job status in tracker
"""

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from config import DATA_DIR, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from notifier import _answer_callback, _edit_message_text, _send
from tracker_manager import create_stub, mark_applied, update_status

logger = logging.getLogger(__name__)
_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

_OFFSET_FILE       = DATA_DIR / "telegram_offset.txt"
_PENDING_APPLY_FILE = DATA_DIR / "pending_apply.json"
_PAUSE_FILE        = DATA_DIR / "pause_until.txt"
_SEARCH_TRIGGER    = DATA_DIR / "search_trigger.json"

_VALID_STATUSES = ["Applied", "Waiting to apply", "Rejected", "Interview", "Offer"]


# ── Offset persistence ────────────────────────────────────────────────────────

def _load_offset() -> int:
    try:
        return int(_OFFSET_FILE.read_text().strip())
    except Exception:
        return 0


def _save_offset(offset: int) -> None:
    try:
        _OFFSET_FILE.write_text(str(offset))
    except Exception as exc:
        logger.warning("Could not save Telegram offset: %s", exc)


# ── Deferred apply queue ──────────────────────────────────────────────────────

def _load_pending() -> list:
    try:
        return json.loads(_PENDING_APPLY_FILE.read_text()) if _PENDING_APPLY_FILE.exists() else []
    except Exception:
        return []


def _save_pending(entries: list) -> None:
    try:
        _PENDING_APPLY_FILE.write_text(json.dumps(entries, indent=2))
    except Exception as exc:
        logger.warning("Could not save pending_apply: %s", exc)


def _drain_pending_queue() -> int:
    """
    Retry mark_applied for every entry in pending_apply.json.
    On success: edit the Telegram message to show ✅ APPLIED.
    On failure after 10 retries: give up and notify.
    Returns number of entries resolved.
    """
    entries = _load_pending()
    if not entries:
        return 0

    still_pending = []
    resolved = 0

    for entry in entries:
        job_id       = entry.get("job_id", "")
        msg_id       = entry.get("msg_id")
        original_text = entry.get("original_text", "")
        applied_at   = entry.get("applied_at", time.strftime("%Y-%m-%d %H:%M"))
        retry_count  = entry.get("retry_count", 0)

        if retry_count >= 10:
            logger.warning("Giving up on pending apply for %s after 10 retries", job_id)
            _send(
                f"⚠️ Could not mark <code>{job_id}</code> as Applied after 10 retries.\n"
                f"Use /setstatus {job_id} Applied or check the tracker manually."
            )
            continue

        # Try the tracker first; if not there, create a stub so it's never lost
        success = mark_applied(job_id, notes=f"Applied via Telegram {applied_at}")
        if not success:
            # Job genuinely not in tracker yet — create a minimal stub row
            created = create_stub(job_id, status="Applied",
                                  notes=f"Applied via Telegram {applied_at} (stub — main run will fill details)")
            success = created

        if success:
            logger.info("Pending apply resolved: %s", job_id)
            if msg_id and original_text:
                new_text = f"✅ <b>APPLIED</b> · {applied_at}\n\n{original_text}"
                _edit_message_text(msg_id, new_text[:4000])
            resolved += 1
            _write_status_override(job_id, "Applied")   # live dashboard overlay
            # Record in follow-up tracker
            try:
                _record_application(job_id, original_text, applied_at)
            except Exception:
                pass
        else:
            entry["retry_count"] = retry_count + 1
            still_pending.append(entry)

    _save_pending(still_pending)
    return resolved


def _record_application(job_id: str, header_text: str, applied_at: str) -> None:
    """Append application to data/applications.json for follow-up tracking."""
    apps_file = DATA_DIR / "applications.json"
    try:
        apps = json.loads(apps_file.read_text()) if apps_file.exists() else []
    except Exception:
        apps = []

    # Don't duplicate
    if any(a.get("job_id") == job_id for a in apps):
        return

    # Extract company + title from header text (best-effort)
    title, company = "", ""
    if header_text:
        import re
        m = re.search(r"<b>(.*?) @ (.*?)</b>", header_text)
        if m:
            title, company = m.group(1), m.group(2)

    apps.append({
        "job_id":     job_id,
        "title":      title,
        "company":    company,
        "applied_at": applied_at,
        "status":     "Applied",
        "followup_7_sent":  False,
        "followup_14_sent": False,
    })
    apps_file.write_text(json.dumps(apps, indent=2))


# ── Callback dispatch ─────────────────────────────────────────────────────────

def _handle_callback(cq: dict) -> None:
    cq_id         = cq.get("id", "")
    data          = cq.get("data", "")
    msg           = cq.get("message", {})
    msg_id        = msg.get("message_id")
    original_text = msg.get("text", "")

    logger.info("Callback: %s", data)

    if data.startswith("applied:"):
        job_id  = data[len("applied:"):].strip()
        now_str = time.strftime("%Y-%m-%d %H:%M")
        success = mark_applied(job_id, notes=f"Applied via Telegram {now_str}")

        if success:
            _answer_callback(cq_id, "✅ Marked as Applied!")
            if msg_id and original_text:
                _edit_message_text(msg_id, f"✅ <b>APPLIED</b> · {now_str}\n\n{original_text}"[:4000])
            _write_status_override(job_id, "Applied")   # live dashboard overlay
            try:
                _record_application(job_id, original_text, now_str)
            except Exception:
                pass
        else:
            # Job not in tracker yet (timing race) — queue for retry
            _answer_callback(cq_id, "⏳ Queued — will confirm shortly")
            entry = {
                "job_id":       job_id,
                "msg_id":       msg_id,
                "original_text": original_text,
                "applied_at":   now_str,
                "retry_count":  0,
            }
            pending = _load_pending()
            if not any(p.get("job_id") == job_id for p in pending):
                pending.append(entry)
                _save_pending(pending)
            logger.info("Applied queued for retry: %s", job_id)

    else:
        _answer_callback(cq_id, "")
        logger.warning("Unknown callback data: %s", data)


# ── Bot commands ──────────────────────────────────────────────────────────────

def _handle_message(msg: dict) -> None:
    """Handle a text message with a / command."""
    text    = (msg.get("text") or "").strip()
    chat_id = str((msg.get("chat") or {}).get("id", ""))

    if chat_id and TELEGRAM_CHAT_ID and chat_id != str(TELEGRAM_CHAT_ID):
        return  # ignore messages from other chats

    if not text.startswith("/"):
        return

    # Strip bot-username suffix (/cmd@MyBot → /cmd)
    parts   = text.split(None, 2)
    command = parts[0].lower().split("@")[0]
    args    = parts[1:] if len(parts) > 1 else []

    logger.info("Command: %s  args=%s", command, args)

    dispatch = {
        "/help":        lambda: _cmd_help(),
        "/start":       lambda: _cmd_help(),
        "/status":      lambda: _cmd_status(),
        "/stats":       lambda: _cmd_stats(),
        "/budget":      lambda: _cmd_budget(),
        "/pause":       lambda: _cmd_pause(args),
        "/resume":      lambda: _cmd_resume(),
        "/search":      lambda: _cmd_search(args),
        "/followup":    lambda: _cmd_followup(),
        "/setstatus":   lambda: _cmd_setstatus(args),
        "/regenerate":  lambda: _cmd_regenerate(args),
        "/pending":     lambda: _cmd_pending(),
        "/gmailsync":   lambda: _cmd_gmailsync(args),
    }

    handler = dispatch.get(command)
    if handler:
        try:
            handler()
        except Exception as exc:
            logger.error("Command %s failed: %s", command, exc)
            _send(f"⚠️ Error running {command}: {exc}")
    else:
        _send(f"Unknown command: <code>{command}</code>\nType /help for available commands.")


# ── Command implementations ───────────────────────────────────────────────────

def _cmd_help():
    _send(
        "🤖 <b>Career Ops — Command Reference</b>\n\n"
        "<b>Tracker</b>\n"
        "/status — Summary: applied, waiting, interviews, offers\n"
        "/stats — Full analytics funnel by source &amp; region\n"
        "/setstatus [job_id] [status] — Update a job's status\n"
        "  Valid: <code>Applied</code> · <code>Waiting to apply</code> · <code>Rejected</code> · <code>Interview</code> · <code>Offer</code>\n"
        "/pending — List all 'Waiting to apply' offers with ✅ Apply buttons\n\n"
        "<b>Documents</b>\n"
        "/regenerate [job_id] — Re-generate tailored resume + cover letter for a job\n\n"
        "<b>Follow-ups</b>\n"
        "/followup — List applications needing a follow-up (≥7 days, no response)\n\n"
        "<b>Scraping</b>\n"
        "/pause [24h | 48h | 7d] — Pause scraping (default: 24h)\n"
        "/resume — Cancel an active pause\n"
        "/search [keyword] — Queue a one-off search for the next scheduled run\n\n"
        "<b>Budget</b>\n"
        "/budget — Anthropic &amp; Apify credit balance\n\n"
        "<b>Gmail</b>\n"
        "/gmailsync [30] — Re-scan Gmail for application emails (default: 30 days back)\n\n"
        "⏰ Scheduled runs: <b>8:00 AM</b> and <b>8:00 PM</b> Paris time."
    )


def _cmd_status():
    from tracker_manager import get_all_jobs
    jobs = get_all_jobs()
    if not jobs:
        _send("📊 <b>Status</b>\n\nTracker is empty — no jobs yet.")
        return

    total     = len(jobs)
    applied   = sum(1 for j in jobs if (j.get("Status") or "").lower() == "applied")
    waiting   = sum(1 for j in jobs if (j.get("Status") or "").lower() == "waiting to apply")
    rejected  = sum(1 for j in jobs if (j.get("Status") or "").lower() == "rejected")
    interview = sum(1 for j in jobs if (j.get("Status") or "").lower() == "interview")
    offer     = sum(1 for j in jobs if (j.get("Status") or "").lower() == "offer")

    recent_applied = [j for j in jobs if (j.get("Status") or "").lower() == "applied"][-3:]
    recent_lines = "\n".join(
        f"  • {j.get('Job Title', '')[:35]} @ {j.get('Company', '')[:20]}"
        for j in reversed(recent_applied)
    )

    pause_line = ""
    if _PAUSE_FILE.exists():
        try:
            until_str = _PAUSE_FILE.read_text().strip()
            until = datetime.fromisoformat(until_str)
            if until > datetime.now(tz=timezone.utc):
                pause_line = f"\n\n⏸️ Scraping paused until {until.strftime('%Y-%m-%d %H:%M UTC')}"
        except Exception:
            pass

    _send(
        f"📊 <b>Tracker Status</b>\n\n"
        f"📁 Total found: <b>{total}</b>\n"
        f"✅ Applied: <b>{applied}</b>\n"
        f"⏳ Waiting: <b>{waiting}</b>\n"
        f"🎯 Interview: <b>{interview}</b>\n"
        f"🎉 Offer: <b>{offer}</b>\n"
        f"❌ Rejected: <b>{rejected}</b>"
        + (f"\n\n<b>Last applied:</b>\n{recent_lines}" if recent_lines else "")
        + pause_line
    )


def _cmd_stats():
    from analytics import generate_analytics_report
    generate_analytics_report()


def _cmd_budget():
    from credit_monitor import get_apify_usage, get_today_summary
    s = get_today_summary()
    a = get_apify_usage()

    acct_rem   = s.get("account_remaining_usd", 0)
    acct_total = s.get("account_total_usd", 0)
    acct_spent = s.get("account_spent_usd", 0)
    pct_left   = (acct_rem / acct_total * 100) if acct_total else 0
    bar        = "🟢" if pct_left >= 50 else "🟡" if pct_left >= 20 else "🔴"

    msg = (
        f"💳 <b>Budget</b>\n\n"
        f"{bar} Anthropic: <b>${acct_rem:.2f}</b> remaining of ${acct_total:.2f}\n"
        f"  All-time spent: ${acct_spent:.2f}  ({pct_left:.0f}% left)\n"
        f"  Today: ${s.get('cost_usd', 0):.4f} in {s.get('calls', 0)} API calls\n"
    )
    if a:
        apify_bar = "🟢" if a["pct_used"] < 50 else "🟡" if a["pct_used"] < 80 else "🔴"
        msg += (
            f"\n{apify_bar} Apify: <b>${a['remaining_usd']:.2f}</b> remaining\n"
            f"  ${a['used_usd']:.2f} / ${a['limit_usd']:.2f} this month ({a['pct_used']:.0f}% used)"
        )
    _send(msg)


def _cmd_pause(args: list):
    duration_str = args[0] if args else "24h"
    hours = 24
    try:
        if duration_str.endswith("h"):
            hours = int(duration_str[:-1])
        elif duration_str.endswith("d"):
            hours = int(duration_str[:-1]) * 24
        elif duration_str.isdigit():
            hours = int(duration_str)
    except ValueError:
        pass
    hours = min(max(hours, 1), 168)

    until = datetime.now(tz=timezone.utc) + timedelta(hours=hours)
    _PAUSE_FILE.write_text(until.isoformat())
    _send(
        f"⏸️ <b>Scraping paused</b> for <b>{hours}h</b>\n"
        f"Resumes: {until.strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"Use /resume to cancel early."
    )


def _cmd_resume():
    if _PAUSE_FILE.exists():
        _PAUSE_FILE.unlink()
        _send("▶️ <b>Pause cancelled</b> — scraping resumes at the next scheduled run.")
    else:
        _send("ℹ️ Scraping is not paused.")


def _cmd_search(args: list):
    keyword = " ".join(args).strip() if args else ""
    if not keyword:
        _send(
            "Usage: /search [keyword]\n"
            "Example: /search reinforcement learning intern\n\n"
            "The keyword will be added to the next scheduled run."
        )
        return
    trigger = {"keyword": keyword, "queued_at": time.strftime("%Y-%m-%d %H:%M UTC")}
    _SEARCH_TRIGGER.write_text(json.dumps(trigger))
    _send(
        f"🔍 <b>Search queued:</b> <i>{keyword}</i>\n"
        f"Will run at the next scheduled scrape.\n"
        f"Results will appear as normal offer notifications."
    )


def _cmd_followup():
    apps_file = DATA_DIR / "applications.json"
    if not apps_file.exists():
        _send("📭 No follow-up data yet. Mark some offers as Applied first.")
        return

    try:
        apps = json.loads(apps_file.read_text())
    except Exception:
        _send("⚠️ Could not read applications.json.")
        return

    now = datetime.now(tz=timezone.utc)
    due = []
    for app in apps:
        if app.get("status", "") in ("Interview", "Offer", "Rejected"):
            continue
        try:
            applied_at = datetime.fromisoformat(app.get("applied_at", ""))
            # Ensure timezone-aware
            if applied_at.tzinfo is None:
                applied_at = applied_at.replace(tzinfo=timezone.utc)
            days_since = (now - applied_at).days
            if days_since >= 7:
                due.append((days_since, app))
        except Exception:
            pass

    if not due:
        _send("✅ No pending follow-ups — all applications are within 7 days or have responses.")
        return

    due.sort(key=lambda x: x[0], reverse=True)
    lines = [f"📬 <b>Pending Follow-ups ({len(due)})</b>\n"]
    for days_ago, app in due[:10]:
        label = "🔴" if days_ago >= 14 else "🟡"
        lines.append(
            f"{label} <b>{app.get('title','?')[:35]}</b> @ {app.get('company','?')[:20]}\n"
            f"   Applied {days_ago} days ago · <code>{app.get('job_id','')}</code>"
        )
    if len(due) > 10:
        lines.append(f"\n...and {len(due) - 10} more.")
    _send("\n\n".join(lines))


def _cmd_setstatus(args: list):
    if len(args) < 2:
        _send(
            "Usage: /setstatus [job_id] [status]\n"
            "Example: /setstatus linkedin_123 Interview\n\n"
            "Valid statuses:\n"
            "  Applied · Waiting to apply · Rejected · Interview · Offer"
        )
        return

    job_id    = args[0]
    status_in = " ".join(args[1:])

    matched = next(
        (s for s in _VALID_STATUSES if s.lower() == status_in.lower()),
        None
    )
    if not matched:
        _send(
            f"❌ Invalid status: <code>{status_in}</code>\n"
            f"Valid: {' · '.join(_VALID_STATUSES)}"
        )
        return

    success = update_status(job_id, matched, notes=f"Status set via Telegram command")
    if success:
        _send(f"✅ <code>{job_id}</code> → <b>{matched}</b>")
        _write_status_override(job_id, matched)   # live dashboard overlay
        # Update applications.json if it exists
        if matched in ("Interview", "Offer", "Rejected"):
            _update_app_status(job_id, matched)
    else:
        _send(
            f"⚠️ Job <code>{job_id}</code> not found in tracker.\n"
            f"Check the dashboard or use /status to see available IDs."
        )


def _cmd_pending():
    """
    List all 'Waiting to apply' offers as individual Telegram messages, each with
    a ✅ Applied button. Lets you bulk-mark everything you've already applied to.
    """
    from tracker_manager import get_all_jobs
    jobs = get_all_jobs()
    pending = [j for j in jobs if (j.get("Status") or "").lower() == "waiting to apply"]

    if not pending:
        _send("✅ No jobs with 'Waiting to apply' status — all caught up!")
        return

    # Most recent first (tracker rows already in insertion order)
    pending = list(reversed(pending))

    _send(
        f"⏳ <b>{len(pending)} offer(s) waiting to apply</b>\n\n"
        f"Tap ✅ Applied on any you've already sent an application for.\n"
        + (f"Showing the 15 most recent — use the dashboard to update the rest." if len(pending) > 15 else "")
    )

    from notifier import _send_with_keyboard
    for job in pending[:15]:
        job_id  = str(job.get("ID", ""))
        title   = (job.get("Job Title") or "")[:50]
        company = (job.get("Company") or "")[:30]
        url     = job.get("Job URL") or ""
        date    = str(job.get("Date Found") or "")[:10]
        source  = job.get("Source") or ""

        link_part = f'\n🔗 <a href="{url}">View listing →</a>' if url and url != "–" else ""
        text = (
            f"⏳ <b>{title}</b>\n"
            f"🏢 {company}  |  📡 {source}  |  📅 {date}"
            f"{link_part}"
        )
        keyboard = [[{"text": "✅ Applied", "callback_data": f"applied:{job_id}"}]]
        _send_with_keyboard(text, keyboard)


def _cmd_regenerate(args: list):
    """
    Re-generate tailored resume + cover letter for a specific job_id.
    Loads the original JD from data/jd_archive/ if available.
    Sends fresh PDFs to Telegram.
    """
    if not args:
        _send(
            "Usage: /regenerate [job_id]\n"
            "Example: /regenerate linkedin_4403632474\n\n"
            "Finds the job in your tracker, loads the archived JD, and "
            "generates fresh tailored documents — useful when you want a "
            "different variant or updated content."
        )
        return

    job_id = args[0].strip()

    # Locate the job in tracker
    from tracker_manager import get_all_jobs
    all_jobs = get_all_jobs()
    tracker_row = next(
        (j for j in all_jobs if str(j.get("ID", "")).strip() == job_id),
        None,
    )

    if not tracker_row:
        _send(
            f"⚠️ Job <code>{job_id}</code> not found in tracker.\n"
            f"Use /status to see available IDs or check the dashboard."
        )
        return

    title   = tracker_row.get("Job Title") or ""
    company = tracker_row.get("Company") or ""

    # Load archived JD (may be empty if job was added before archiving was live)
    from jd_archive import load_jd
    description = load_jd(job_id)
    if not description:
        logger.info("No archived JD for %s — generating from title/company only.", job_id)

    job_for_gen = {
        "job_id":      job_id,
        "title":       title,
        "company":     company,
        "location":    tracker_row.get("Location") or "",
        "region":      tracker_row.get("Region") or "",
        "url":         tracker_row.get("Job URL") or "",
        "source":      tracker_row.get("Source") or "",
        "description": description,
    }

    jd_note = "with archived JD" if description else "from title/company only (no archived JD)"
    _send(
        f"🔄 Regenerating documents for <b>{title}</b> @ {company}\n"
        f"<i>{jd_note}</i>\n\nThis takes ~30 seconds…"
    )

    try:
        from doc_generator import generate_documents
        from notifier import send_documents
        resume_path, cover_path = generate_documents(job_for_gen)
        send_documents(job_for_gen, resume_path, cover_path)
        logger.info("Regenerated docs for %s", job_id)
    except RuntimeError as exc:
        _send(f"🚨 Budget exhausted — cannot regenerate: {exc}")
    except Exception as exc:
        logger.error("_cmd_regenerate failed for %s: %s", job_id, exc)
        _send(f"❌ Regeneration failed: {exc}")


def _cmd_gmailsync(args: list):
    """Re-scan Gmail for application status emails."""
    days = 30
    if args:
        try:
            days = max(1, min(int(args[0]), 180))
        except ValueError:
            pass

    _send(f"📧 Scanning Gmail for the last <b>{days} days</b>… (this may take a moment)")
    try:
        from gmail_tracker import sync_gmail_statuses
        changes = sync_gmail_statuses(days_back=days)
        if not changes:
            _send(f"✅ Gmail scan complete — no new status changes found in the last {days} days.\n\n"
                  f"Tip: If you applied to jobs and didn't get confirmation emails, "
                  f"use /pending to manually mark them as Applied.")
    except Exception as exc:
        logger.error("_cmd_gmailsync failed: %s", exc)
        _send(f"❌ Gmail sync failed: {exc}")


def _update_app_status(job_id: str, new_status: str) -> None:
    """Sync status change back into applications.json."""
    apps_file = DATA_DIR / "applications.json"
    if not apps_file.exists():
        return
    try:
        apps = json.loads(apps_file.read_text())
        for app in apps:
            if app.get("job_id") == job_id:
                app["status"] = new_status
        apps_file.write_text(json.dumps(apps, indent=2))
    except Exception:
        pass


# ── Webhook / polling helpers ─────────────────────────────────────────────────

def _ensure_polling_mode() -> None:
    """
    Delete any registered webhook so Telegram delivers updates via getUpdates.
    Safe to call on every run — no-op if no webhook is set.
    A webhook (even a dead one) silently swallows all updates and makes getUpdates
    return nothing, which is the most common cause of the bot appearing unresponsive.
    """
    if not TELEGRAM_BOT_TOKEN:
        return
    try:
        info = requests.get(f"{_API}/getWebhookInfo", timeout=10).json()
        url  = (info.get("result") or {}).get("url", "")
        if url:
            r = requests.post(f"{_API}/deleteWebhook",
                              json={"drop_pending_updates": False}, timeout=10)
            logger.info("Webhook deleted (was: %s) → polling mode active.", url)
        else:
            logger.debug("No webhook set — polling mode confirmed.")
    except Exception as exc:
        logger.warning("_ensure_polling_mode failed: %s", exc)


def _write_status_override(job_id: str, status: str) -> None:
    """
    Write a status change to data/statuses.json so the dashboard client-side
    overlay reflects it on the next page load (without waiting for a full rebuild).
    """
    statuses_path = DATA_DIR / "statuses.json"
    try:
        overrides = json.loads(statuses_path.read_text()) if statuses_path.exists() else {}
    except Exception:
        overrides = {}
    overrides[str(job_id)] = status
    try:
        statuses_path.write_text(json.dumps(overrides, indent=2))
    except Exception as exc:
        logger.warning("_write_status_override failed: %s", exc)


# ── Update fetcher ────────────────────────────────────────────────────────────

def _get_updates(offset: int, timeout: int = 0) -> list:
    if not TELEGRAM_BOT_TOKEN:
        return []
    try:
        r = requests.get(
            f"{_API}/getUpdates",
            params={
                "offset":          offset,
                "timeout":         timeout,
                "allowed_updates": ["callback_query", "message"],
            },
            timeout=timeout + 10,
        )
        r.raise_for_status()
        return r.json().get("result", [])
    except requests.RequestException as exc:
        logger.warning("getUpdates failed: %s", exc)
        return []


# ── Public API ────────────────────────────────────────────────────────────────

def process_pending_callbacks() -> int:
    """
    One-shot flush: drain all queued updates + retry deferred applies.
    Called at the start of main.py. Returns number of items processed.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return 0

    # Ensure no webhook is stealing updates (silent cause of button/command failures)
    _ensure_polling_mode()

    # First: retry any previously deferred applied callbacks
    resolved = _drain_pending_queue()

    # Then: process new updates
    offset    = _load_offset()
    updates   = _get_updates(offset, timeout=0)
    processed = 0

    for upd in updates:
        offset = upd["update_id"] + 1
        if "callback_query" in upd:
            try:
                _handle_callback(upd["callback_query"])
                processed += 1
            except Exception as exc:
                logger.error("Callback error: %s", exc)
        elif "message" in upd:
            try:
                _handle_message(upd["message"])
                processed += 1
            except Exception as exc:
                logger.error("Message error: %s", exc)

    if updates:
        _save_offset(offset)

    total = resolved + processed
    if total:
        logger.info("Processed %d update(s) (%d deferred resolved, %d new).", total, resolved, processed)
    return total


def run_polling_loop(poll_timeout: int = 30) -> None:
    """Persistent long-polling loop. Blocks forever (run from bot.py). Ctrl+C to stop."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Telegram not configured — bot cannot start.")
        return

    offset = _load_offset()
    logger.info("Bot polling started (timeout=%ds). Press Ctrl+C to stop.", poll_timeout)

    # Drain deferred queue on startup
    _drain_pending_queue()

    while True:
        try:
            updates = _get_updates(offset, timeout=poll_timeout)
            for upd in updates:
                offset = upd["update_id"] + 1
                if "callback_query" in upd:
                    try:
                        _handle_callback(upd["callback_query"])
                    except Exception as exc:
                        logger.error("Callback error: %s", exc)
                elif "message" in upd:
                    try:
                        _handle_message(upd["message"])
                    except Exception as exc:
                        logger.error("Message error: %s", exc)
            if updates:
                _save_offset(offset)
        except KeyboardInterrupt:
            logger.info("Bot polling stopped.")
            _save_offset(offset)
            break
        except Exception as exc:
            logger.error("Polling loop error: %s — retrying in 5s", exc)
            time.sleep(5)
