"""
callback_handler.py — Process Telegram callback queries from inline buttons.

Two usage modes:

  1. One-shot flush (called at the start of every main.py run):
       from callback_handler import process_pending_callbacks
       process_pending_callbacks()

  2. Persistent real-time polling (run bot.py separately on your Mac):
       python3 bot.py

Handled callbacks:
  - applied:{job_id}  → marks job as Applied in tracker, sends confirmation
"""

import logging
import time
from pathlib import Path

import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DATA_DIR
from notifier import _answer_callback, _edit_message_text, _send
from tracker_manager import mark_applied

logger = logging.getLogger(__name__)
_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

_OFFSET_FILE = DATA_DIR / "telegram_offset.txt"


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


# ── Callback dispatch ─────────────────────────────────────────────────────────

def _handle_callback(cq: dict) -> None:
    """Process a single callback_query object."""
    cq_id   = cq.get("id", "")
    data    = cq.get("data", "")
    msg     = cq.get("message", {})
    msg_id  = msg.get("message_id")
    original_text = msg.get("text", "")

    logger.info("Callback: %s", data)

    if data.startswith("applied:"):
        job_id = data[len("applied:"):].strip()
        now_str = time.strftime("%Y-%m-%d %H:%M")
        success = mark_applied(job_id, notes=f"Applied via Telegram {now_str}")

        if success:
            _answer_callback(cq_id, "✅ Marked as Applied!")
            # Edit the original header message to show confirmation
            if msg_id and original_text:
                new_text = f"✅ <b>APPLIED</b> · {now_str}\n\n{original_text}"
                _edit_message_text(msg_id, new_text[:4000])
            else:
                _send(f"✅ <b>Marked as Applied</b> — job ID <code>{job_id}</code>")
        else:
            _answer_callback(cq_id, "⚠️ Job not found in tracker")
            _send(f"⚠️ Could not find job <code>{job_id}</code> in tracker.")

    else:
        # Unknown callback — acknowledge to prevent spinner
        _answer_callback(cq_id, "")
        logger.warning("Unknown callback data: %s", data)


# ── Update fetcher ────────────────────────────────────────────────────────────

def _get_updates(offset: int, timeout: int = 0) -> list:
    """
    Fetch updates from Telegram.
    timeout=0  → instant (for one-shot flush)
    timeout>0  → long-poll (for persistent bot.py loop)
    """
    if not TELEGRAM_BOT_TOKEN:
        return []
    try:
        r = requests.get(
            f"{_API}/getUpdates",
            params={"offset": offset, "timeout": timeout, "allowed_updates": ["callback_query"]},
            timeout=timeout + 5,
        )
        r.raise_for_status()
        return r.json().get("result", [])
    except requests.RequestException as exc:
        logger.warning("getUpdates failed: %s", exc)
        return []


# ── Public API ────────────────────────────────────────────────────────────────

def process_pending_callbacks() -> int:
    """
    One-shot flush: drain all queued callback_queries without waiting.
    Called at the start of main.py. Returns number of callbacks processed.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return 0

    offset   = _load_offset()
    updates  = _get_updates(offset, timeout=0)
    processed = 0

    for upd in updates:
        offset = upd["update_id"] + 1
        if "callback_query" in upd:
            try:
                _handle_callback(upd["callback_query"])
                processed += 1
            except Exception as exc:
                logger.error("Callback handling error: %s", exc)

    if updates:
        _save_offset(offset)

    if processed:
        logger.info("Processed %d pending Telegram callback(s).", processed)
    return processed


def run_polling_loop(poll_timeout: int = 30) -> None:
    """
    Persistent long-polling loop. Blocks forever (run from bot.py).
    Ctrl+C to stop.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Telegram not configured — bot cannot start.")
        return

    offset = _load_offset()
    logger.info("Bot polling started (timeout=%ds). Press Ctrl+C to stop.", poll_timeout)

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
            if updates:
                _save_offset(offset)
        except KeyboardInterrupt:
            logger.info("Bot polling stopped.")
            _save_offset(offset)
            break
        except Exception as exc:
            logger.error("Polling loop error: %s — retrying in 5s", exc)
            time.sleep(5)
