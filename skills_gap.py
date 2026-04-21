"""
skills_gap.py — Weekly skills gap analysis from scored jobs.

How it works:
  1. Every run, all scored jobs (score, title, company, desc snippet) are appended
     to data/scored_jobs.jsonl.
  2. Once per week (triggered from main.py on Mondays), Claude analyzes the last
     7 days of scored jobs to identify:
       - Most common skills in 5-6/10 jobs that the candidate lacks
       - Recurring keywords in top-scoring (9-10) jobs
       - Recommended learning priorities
  3. Report sent to Telegram.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

import anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, DATA_DIR
from credit_monitor import check_budget_alert, record_usage
from notifier import _send

logger = logging.getLogger(__name__)

SCORED_JOBS_PATH = DATA_DIR / "scored_jobs.jsonl"

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


# ── Data persistence ──────────────────────────────────────────────────────────

def save_scored_jobs(jobs: List[dict]) -> None:
    """
    Append all scored jobs (compatible + rejected) to scored_jobs.jsonl.
    Stores only the fields needed for analysis (not full descriptions).
    Called from main.py after filter_jobs().
    """
    if not jobs:
        return
    now = datetime.now(tz=timezone.utc).isoformat()
    try:
        with open(str(SCORED_JOBS_PATH), "a", encoding="utf-8") as f:
            for j in jobs:
                record = {
                    "ts":      now,
                    "title":   j.get("title", "")[:80],
                    "company": j.get("company", "")[:60],
                    "source":  j.get("source", ""),
                    "region":  j.get("region", ""),
                    "score":   j.get("relevance_score", 0),
                    "reason":  j.get("relevance_reason", "")[:120],
                    # First 300 chars of description — enough for keyword extraction
                    "desc":    (j.get("description") or "")[:300],
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.error("save_scored_jobs failed: %s", exc)


def _load_recent_jobs(days: int = 7) -> List[dict]:
    """Load scored jobs from the last `days` days."""
    if not SCORED_JOBS_PATH.exists():
        return []
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    jobs = []
    try:
        with open(str(SCORED_JOBS_PATH), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    ts  = datetime.fromisoformat(rec.get("ts", "2000-01-01T00:00:00+00:00"))
                    if ts >= cutoff:
                        jobs.append(rec)
                except Exception:
                    continue
    except Exception as exc:
        logger.error("_load_recent_jobs failed: %s", exc)
    return jobs


# ── Analysis ──────────────────────────────────────────────────────────────────

def analyze_skills_gap(days: int = 7) -> None:
    """
    Run weekly skills gap analysis and send the report to Telegram.
    Analyzes jobs from the past `days` days.
    Skips if fewer than 20 jobs available (not enough signal).
    """
    alert_level, _ = check_budget_alert()
    if alert_level == "danger":
        logger.warning("Budget exhausted — skipping skills gap analysis.")
        return

    jobs = _load_recent_jobs(days)
    if len(jobs) < 20:
        logger.info("Skills gap: only %d jobs in window — skipping (need >=20).", len(jobs))
        return

    # Split by score band
    top_jobs     = [j for j in jobs if j["score"] >= 9]    # ideal roles
    good_jobs    = [j for j in jobs if 7 <= j["score"] <= 8]
    adjacent_jobs = [j for j in jobs if 5 <= j["score"] <= 6]  # adjacent — has skills we're missing
    total = len(jobs)

    logger.info(
        "Skills gap analysis: %d jobs total  (9-10: %d, 7-8: %d, 5-6: %d)",
        total, len(top_jobs), len(good_jobs), len(adjacent_jobs),
    )

    # Build a compact summary for Claude
    def _job_summary(j: dict) -> str:
        return f"[{j['score']}/10] {j['title']} @ {j['company']} ({j['region']}) | {j['reason']} | {j['desc'][:150]}"

    summary_lines = (
        [f"SCORE 9-10 (ideal roles, {len(top_jobs)}):\n" + "\n".join(_job_summary(j) for j in top_jobs[:20])]
        + [f"\nSCORE 5-6 (adjacent, {len(adjacent_jobs)}):\n" + "\n".join(_job_summary(j) for j in adjacent_jobs[:25])]
    )
    jobs_text = "\n".join(summary_lines)[:4000]

    prompt = (
        f"You are analyzing {total} internship listings scored against a ML/AI candidate "
        f"(EURECOM Data Science Engineer, strong in PyTorch/LoRA fine-tuning/CLIP/CV/NLP).\n\n"
        f"Below are listings from the past {days} days, split by relevance score:\n\n"
        f"{jobs_text}\n\n"
        f"Your analysis task:\n"
        f"1. SKILLS GAP: What skills appear frequently in 5-6/10 jobs that the candidate does NOT currently have? "
        f"   (Only flag real gaps, not things the candidate has like PyTorch, CLIP, LoRA, etc.)\n"
        f"2. TOP KEYWORDS: What terms appear most in 9-10/10 ideal roles? "
        f"   These should be reinforced in every resume.\n"
        f"3. QUICK WINS: Which 2-3 skills/certs/projects would most expand the candidate's eligible pool?\n"
        f"4. REGIONAL PATTERNS: Any notable geographic trends (e.g. certain skills valued more in Asia vs EU)?\n\n"
        f"Format your response as a Telegram HTML message (use <b> for bold, - for bullets). "
        f"Be specific and actionable. Max 400 words."
    )

    try:
        msg = _get_client().messages.create(
            model=CLAUDE_MODEL,
            max_tokens=700,
            messages=[{"role": "user", "content": prompt}],
        )
        record_usage(msg.usage.input_tokens, msg.usage.output_tokens, label="skills_gap")
        analysis = msg.content[0].text.strip()
    except Exception as exc:
        logger.error("Skills gap Claude call failed: %s", exc)
        return

    report = (
        f"📊 <b>Weekly Skills Gap Analysis</b>\n"
        f"<i>{total} jobs analyzed over past {days} days</i>\n"
        f"Top: {len(top_jobs)} | Good: {len(good_jobs)} | Adjacent: {len(adjacent_jobs)}\n\n"
        f"{analysis}"
    )
    _send(report)
    logger.info("Skills gap report sent to Telegram.")


# ── Weekly trigger check ──────────────────────────────────────────────────────

def should_run_weekly_analysis() -> bool:
    """
    Returns True if the weekly analysis should run today.
    Triggers on Mondays (weekday=0) to give a fresh start-of-week view.
    Uses a last-run file to prevent running twice on the same day.
    """
    today = datetime.now(tz=timezone.utc)
    if today.weekday() != 0:   # 0 = Monday
        return False

    last_run_file = DATA_DIR / "skills_gap_last_run.txt"
    today_str = today.strftime("%Y-%m-%d")

    if last_run_file.exists():
        try:
            if last_run_file.read_text().strip() == today_str:
                return False
        except Exception:
            pass

    try:
        last_run_file.write_text(today_str)
    except Exception:
        pass

    return True
