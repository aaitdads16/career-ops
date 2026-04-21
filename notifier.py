"""
Telegram notifications for the Internship Finder.
Sends text reports + PDF attachments (resume + cover letter) per compatible offer.
Supports inline keyboards for the "✅ Applied" button (requires bot.py polling loop).
"""

import logging
from typing import List, Optional

import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)
_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


# ── Core send helpers ─────────────────────────────────────────────────────────

def _send(text: str, parse_mode: str = "HTML", disable_preview: bool = True) -> bool:
    """Send a plain text message to the Telegram chat."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram not configured — skipping notification.")
        return False
    try:
        r = requests.post(
            f"{_API}/sendMessage",
            json={
                "chat_id":                  TELEGRAM_CHAT_ID,
                "text":                     text,
                "parse_mode":               parse_mode,
                "disable_web_page_preview": disable_preview,
            },
            timeout=15,
        )
        r.raise_for_status()
        return True
    except requests.RequestException as exc:
        logger.warning("Telegram sendMessage failed: %s", exc)
        return False


def _send_with_keyboard(
    text: str,
    keyboard: list,
    parse_mode: str = "HTML",
    disable_preview: bool = True,
) -> Optional[int]:
    """
    Send a message with an inline keyboard.
    Returns the Telegram message_id (needed to edit the button later) or None on failure.

    keyboard format:
        [[{"text": "✅ Applied", "callback_data": "applied:job-id-here"}]]
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return None
    try:
        r = requests.post(
            f"{_API}/sendMessage",
            json={
                "chat_id":                  TELEGRAM_CHAT_ID,
                "text":                     text,
                "parse_mode":               parse_mode,
                "disable_web_page_preview": disable_preview,
                "reply_markup":             {"inline_keyboard": keyboard},
            },
            timeout=15,
        )
        r.raise_for_status()
        return r.json().get("result", {}).get("message_id")
    except requests.RequestException as exc:
        logger.warning("Telegram sendMessage (keyboard) failed: %s", exc)
        return None


def _edit_message_text(message_id: int, new_text: str, parse_mode: str = "HTML") -> bool:
    """Edit an existing message (used to show '✅ Applied!' confirmation)."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        r = requests.post(
            f"{_API}/editMessageText",
            json={
                "chat_id":    TELEGRAM_CHAT_ID,
                "message_id": message_id,
                "text":       new_text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        r.raise_for_status()
        return True
    except requests.RequestException as exc:
        logger.warning("Telegram editMessageText failed: %s", exc)
        return False


def _answer_callback(callback_query_id: str, text: str = "") -> bool:
    """Acknowledge a callback query (removes the loading spinner in Telegram)."""
    if not TELEGRAM_BOT_TOKEN:
        return False
    try:
        r = requests.post(
            f"{_API}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id, "text": text},
            timeout=10,
        )
        r.raise_for_status()
        return True
    except requests.RequestException:
        return False


def _send_file(file_path, caption: str = "") -> bool:
    """Send a file to the Telegram chat."""
    from pathlib import Path
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    if not file_path or not Path(str(file_path)).exists():
        logger.warning("File not found for Telegram upload: %s", file_path)
        return False
    try:
        with open(str(file_path), "rb") as f:
            r = requests.post(
                f"{_API}/sendDocument",
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption[:1024], "parse_mode": "HTML"},
                files={"document": f},
                timeout=60,
            )
        r.raise_for_status()
        return True
    except requests.RequestException as exc:
        logger.warning("Telegram sendDocument failed (%s): %s", file_path, exc)
        return False


# ── Document delivery ────────────────────────────────────────────────────────

def send_documents(job: dict, resume_path: str, cover_path: str):
    """
    Send resume + cover letter PDFs for a single job as Telegram attachments.
    Called once per compatible offer after document generation.
    Attaches an inline "✅ Mark as Applied" button — handled by bot.py.
    """
    if not resume_path and not cover_path:
        return

    _src_emoji = {"Indeed": "🔵", "LinkedIn": "🔷", "Glassdoor": "🟢",
                  "Wellfound": "🟠", "Google Jobs": "🔴"}
    source_emoji = _src_emoji.get(job.get("source", ""), "⚪️")
    score = job.get("relevance_score", "")
    score_tag = f" ⭐{score}/10" if score else ""

    # ATS score line (set by doc_generator)
    ats_score = job.get("ats_score")
    ats_line  = ""
    if ats_score is not None:
        bar = "🟢" if ats_score >= 80 else "🟡" if ats_score >= 60 else "🔴"
        ats_line = f"\n{bar} ATS match: <b>{ats_score}%</b>"
        missing = job.get("ats_missing", [])
        if missing:
            ats_line += f" · missing: {', '.join(missing[:4])}"

    # Outreach line (set by outreach_generator)
    outreach_line = ""
    if job.get("linkedin_outreach"):
        outreach_line = f"\n\n💬 <b>LinkedIn message:</b>\n{job['linkedin_outreach']}"

    header = (
        f"📄 <b>{job.get('title', '')} @ {job.get('company', '')}</b>{score_tag}\n"
        f"{source_emoji} {job.get('location', '')}  |  "
        f"<a href=\"{job.get('url', '')}\">Apply →</a>"
        f"{ats_line}"
        f"{outreach_line}"
    )

    # Send header with "✅ Mark as Applied" inline button
    job_id = job.get("job_id", "")
    if job_id:
        keyboard = [[{"text": "✅ Mark as Applied", "callback_data": f"applied:{job_id}"}]]
        msg_id = _send_with_keyboard(header, keyboard)
        # Store message_id so bot.py can edit it after confirmation
        job["_tg_header_msg_id"] = msg_id
    else:
        _send(header)

    if resume_path:
        _send_file(
            resume_path,
            caption=f"📋 Resume — {job.get('title', '')} @ {job.get('company', '')}",
        )

    if cover_path:
        _send_file(
            cover_path,
            caption=f"✉️ Cover Letter — {job.get('title', '')} @ {job.get('company', '')}",
        )


# ── Notification functions ────────────────────────────────────────────────────

def notify_single_job(job: dict, resume_path=None, cover_path=None):
    """
    High-priority text alert for a same-hour offer (no file attachments).
    resume_path / cover_path accepted for API compatibility but not sent.
    """
    source_emoji = {
        "Indeed":    "🔵",
        "LinkedIn":  "🔷",
        "Glassdoor": "🟢",
        "Wellfound": "🟠",
    }.get(job.get("source", ""), "⚪️")

    posted = ""
    if job.get("posted_at"):
        posted = job["posted_at"].strftime("%H:%M UTC")

    score_line = ""
    if job.get("relevance_score"):
        score_line = f"⭐ Match: <b>{job['relevance_score']}/10</b> — {job.get('relevance_reason', '')}\n"

    text = (
        f"🔥 <b>SAME-HOUR OFFER — Apply now!</b>\n\n"
        f"{source_emoji} <b>{job['title']}</b>\n"
        f"🏢 {job['company']}\n"
        f"📍 {job['location']}  |  🌍 {job.get('region', '')}\n"
        f"🕐 Posted: {posted}\n"
        f"{score_line}"
        f"🔗 <a href=\"{job['url']}\">View &amp; Apply</a>\n\n"
        f"📎 Resume &amp; cover letter attached below."
    )
    _send(text)


def notify_new_jobs(
    jobs: List[dict],
    same_hour_jobs: Optional[List[dict]] = None,
    scraped_total: int = 0,
    rejected_count: int = 0,
):
    """
    Report of all compatible offers found this run — text only, no files.
    `jobs` should be the non-same-hour offers (same_hour_jobs already sent as
    priority alerts). If same_hour_jobs is provided they are listed first with
    a 🔥 badge so the full report is still complete but not duplicated.
    """
    same_hour_jobs = same_hour_jobs or []
    all_jobs = same_hour_jobs + jobs   # same-hour first, then the rest

    if not all_jobs:
        return

    total_compatible = len(all_jobs)

    # Counts by source and region (over all compatible jobs)
    sources: dict = {}
    regions: dict = {}
    for j in all_jobs:
        s = j.get("source", "?")
        r = j.get("region", "?")
        sources[s] = sources.get(s, 0) + 1
        regions[r] = regions.get(r, 0) + 1

    _src_emoji = {"Indeed": "🔵", "LinkedIn": "🔷", "Glassdoor": "🟢", "Wellfound": "🟠"}
    source_line = " · ".join(
        f"{_src_emoji.get(s, '⚪️')} {s} {n}" for s, n in sources.items()
    )
    region_line = (
        f"🇪🇺 EU: {regions.get('Europe', 0)}  "
        f"🌏 Asia: {regions.get('Asia', 0)}  "
        f"🇺🇸 US/CA: {regions.get('USA_Canada', 0)}  "
        f"🌎 LatAm: {regions.get('South_America', 0)}  "
        f"🌍 ME: {regions.get('Middle_East', 0)}"
    )

    filter_line = ""
    if scraped_total:
        filter_line = (
            f"🔍 Scraped: {scraped_total}  →  ✅ Compatible: {total_compatible}"
            f"  ✗ Filtered: {rejected_count}\n"
        )

    same_hour_note = ""
    if same_hour_jobs:
        same_hour_note = f"🔥 {len(same_hour_jobs)} same-hour offer(s) — PDFs already sent above\n"

    header = (
        f"💼 <b>{total_compatible} compatible internship{'s' if total_compatible > 1 else ''} found</b>\n"
        f"{region_line}\n"
        f"📡 {source_line}\n"
        f"{filter_line}"
        f"{same_hour_note}\n"
    )

    same_hour_ids = {id(j) for j in same_hour_jobs}
    lines = []
    for i, j in enumerate(all_jobs, 1):
        score = j.get("relevance_score", "")
        score_tag = f" ⭐{score}/10" if score else ""
        fire_tag = " 🔥" if id(j) in same_hour_ids else ""

        reason = j.get("relevance_reason", "")
        reason_line = f"\n   💡 {reason}" if reason else ""

        lines.append(
            f"{i}.{score_tag}{fire_tag} <b>{j['title']}</b> @ {j['company']}\n"
            f"   📍 {j['location']}  |  <a href=\"{j['url']}\">Apply →</a>"
            f"{reason_line}"
        )

    body = "\n\n".join(lines)
    for chunk in _split_message(header + body):
        _send(chunk)


def notify_run_complete(
    new_count: int,
    total_count: int,
    error: Optional[str] = None,
    cost_summary: Optional[dict] = None,
    scraped_total: int = 0,
    rejected_count: int = 0,
):
    """Run summary — sent after every execution."""
    if error:
        _send(
            f"❌ <b>Internship Finder — Run Failed</b>\n\n"
            f"<code>{error}</code>\n\n"
            f"Check <code>data/run.log</code> for details."
        )
        return

    filter_line = ""
    if scraped_total:
        filter_line = (
            f"\nScraped: {scraped_total}  →  Compatible: {new_count}"
            f"  |  Filtered: {rejected_count}"
        )

    cost_line = ""
    if cost_summary:
        # Account balance (real remaining credit)
        acct_rem   = cost_summary.get("account_remaining_usd")
        acct_total = cost_summary.get("account_total_usd", 0)
        acct_spent = cost_summary.get("account_spent_usd", 0)
        today_usd  = cost_summary.get("cost_usd", 0)
        today_calls = cost_summary.get("calls", 0)

        if acct_rem is not None:
            pct_left = (acct_rem / acct_total * 100) if acct_total else 0
            bar_emoji = "🟢" if pct_left >= 50 else "🟡" if pct_left >= 20 else "🔴"
            cost_line = (
                f"\n\n💳 <b>Anthropic balance</b>\n"
                f"{bar_emoji} <b>${acct_rem:.2f}</b> remaining"
                f" of ${acct_total:.2f} total ({pct_left:.0f}% left)\n"
                f"Today: ${today_usd:.4f} in {today_calls} calls"
                f" · All-time: ${acct_spent:.2f} spent"
            )
        else:
            # Fallback if account tracking not yet populated
            daily_rem = cost_summary.get("remaining_usd", 0)
            cost_line = (
                f"\n\n💳 <b>Credits</b>\n"
                f"Today: <b>${today_usd:.4f}</b> (${daily_rem:.4f} of daily cap left)"
            )

        if cost_summary.get("apify"):
            a = cost_summary["apify"]
            cost_line += (
                f"\nApify: <b>${a['remaining_usd']:.2f}</b> left"
                f" (${a['used_usd']:.2f} / ${a['limit_usd']:.2f} this month"
                f" · {a['pct_used']:.0f}% used)"
            )

    _send(
        f"✅ <b>Run complete</b>\n\n"
        f"New compatible offers: <b>{new_count}</b>\n"
        f"Total in tracker: <b>{total_count}</b>"
        f"{filter_line}"
        f"{cost_line}\n\n"
        f"📎 All resumes &amp; cover letters sent above as PDF attachments."
    )


def notify_budget_alert(message: str, priority: int = 4):
    """Credit / budget warning."""
    emoji = "🚨" if priority >= 5 else "⚠️"
    _send(f"{emoji} <b>Anthropic Credit Alert</b>\n\n{message}")


def notify_startup():
    """Sent once when the system first connects — confirms bot is working."""
    _send(
        "🤖 <b>Internship Finder is connected!</b>\n\n"
        "You will receive:\n"
        "• 🔥 Same-hour offer alerts\n"
        "• 💼 Report of all compatible offers per run (with scores)\n"
        "• 📎 Resume + cover letter PDFs per offer\n"
        "• ✅ Run completion summary\n"
        "• ⚠️ Credit alerts if budget is running low\n\n"
        "Next scheduled runs: <b>8:00 AM</b> and <b>8:00 PM</b> Paris time."
    )


# ── Utility ───────────────────────────────────────────────────────────────────

def _split_message(text: str, limit: int = 4000) -> List[str]:
    """Split a long message into chunks fitting Telegram's 4096 char limit."""
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        chunk = text[:limit]
        split_at = chunk.rfind("\n")
        if split_at > limit // 2:
            chunk = chunk[:split_at]
        chunks.append(chunk)
        text = text[len(chunk):]
    return chunks
