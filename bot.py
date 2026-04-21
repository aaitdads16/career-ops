#!/usr/bin/env python3
"""
bot.py — Persistent Telegram bot for interactive career-ops features.

Run this on your Mac (NOT on GitHub Actions) for real-time button handling:
    python3 bot.py

When you tap "✅ Mark as Applied" on a job notification, this bot:
  1. Marks the job as Applied in tracker.xlsx immediately
  2. Edits the original Telegram message to show "✅ APPLIED"
  3. Logs the application date

The main.py pipeline also calls process_pending_callbacks() at startup
to flush any button presses that happened while bot.py wasn't running.

Stop with Ctrl+C.
"""

import logging
import sys
from pathlib import Path

# Make sure imports resolve from this directory
sys.path.insert(0, str(Path(__file__).parent))

from callback_handler import run_polling_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

if __name__ == "__main__":
    run_polling_loop(poll_timeout=30)
