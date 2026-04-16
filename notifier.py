"""
Telegram notifications for the Internship Finder.
Sends text reports + PDF attachments (resume + cover letter) per compatible offer.
"""

import logging
from typing import List, Optional

import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)
_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


# ── Core send helper ──────────────────────────────────────────────────────────

def _send(text: str, parse_mode: str = "HTML", disable_preview: bool = True) -> bool:
    """Send a text message to the Telegram chat."""
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
    """
    if not resume_path and not cover_path:
        return

    _src_emoji = {"Indeed": "🔵", "LinkedIn": "🔷", "Glassdoor": "🟢", "Wellfound": "🟠"}
    source_emoji = _src_emoji.get(job.get("source", ""), "⚪️")
    score = job.get("relevance_score", "")
    score_tag = f" ⭐{score}/10" if score else ""

    header = (
        f"📄 <b>{job.get('title', '')} @ {job.get('company', '')}</b>{score_tag}\n"
        f"{source_emoji} {job.get('location', '')}  |  "
        f"<a href=\"{job.get('url', '')}\">Apply →</a>"
    )
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


def notify_new_jobs(jobs: List[dict], scraped_total: int = 0, rejected_count: int = 0):
    """
    Report of all compatible offers found this run — text only, no files.
    Shows relevance scores and apply links.
    """
    if not jobs:
        return

    count = len(jobs)

    # Counts by source and region
    sources: dict = {}
    regions: dict = {}
    for j in jobs:
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
        f"🇺🇸 US/CA: {regions.get('USA_Canada', 0)}"
    )

    filter_line = ""
    if scraped_total:
        filter_line = (
            f"🔍 Scraped: {scraped_total}  →  ✅ Compatible: {count}"
            f"  ✗ Filtered out: {rejected_count}\n"
        )

    header = (
        f"💼 <b>{count} compatible internship{'s' if count > 1 else ''} found</b>\n"
        f"{region_line}\n"
        f"📡 {source_line}\n"
        f"{filter_line}\n"
    )

    lines = []
    for i, j in enumerate(jobs, 1):
        score = j.get("relevance_score", "")
        score_tag = f" ⭐{score}/10" if score else ""

        same_hour_tag = ""
        if j.get("posted_at"):
            import datetime
            if j["posted_at"].hour == datetime.datetime.utcnow().hour:
                same_hour_tag = " 🔥"

        reason = j.get("relevance_reason", "")
        reason_line = f"\n   💡 {reason}" if reason else ""

        lines.append(
            f"{i}.{score_tag}{same_hour_tag} <b>{j['title']}</b> @ {j['company']}\n"
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
        cost_line = (
            f"\n💰 Claude today: <b>${cost_summary['cost_usd']:.4f}</b>"
            f" / ${cost_summary['budget_usd']:.2f}"
            f" ({cost_summary['pct_used']:.0f}%)"
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
