"""
outreach_generator.py — LinkedIn cold outreach message generator.

For every compatible job scoring >= 8/10, generates a personalized 3-line
LinkedIn connection request message using Claude.

The message is:
  - Under 300 characters (LinkedIn connection note limit)
  - Specific to the role and company
  - Non-generic (no "I'm interested in your company" boilerplate)
  - Written in first person, professional but direct tone

The generated message is stored in job["linkedin_outreach"] and included
in the Telegram notification so you can copy-paste it instantly.
"""

import logging
from typing import Optional

import anthropic

from config import ANTHROPIC_API_KEY, CANDIDATE, CLAUDE_MODEL
from credit_monitor import check_budget_alert, record_usage

logger = logging.getLogger(__name__)

_client = None
_OUTREACH_MIN_SCORE = 8   # Only generate for strong matches

# Threshold below which we skip outreach generation to save budget
_OUTREACH_MIN_SCORE = 8


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


_CANDIDATE_BRIEF = (
    f"Aymane Ait Dads — 3rd-year Data Science Engineering student at EURECOM (Master-level). "
    f"Top 10% NVIDIA Nemotron Kaggle competition (30B LoRA fine-tuning), "
    f"1st in EURECOM class for AI-image detection (CLIP ViT fine-tuning, submitted NTIRE 2026 @ CVPR), "
    f"ML internship at Orange Maroc. Stack: PyTorch, Unsloth, PEFT, OpenCLIP, Python."
)


def generate_outreach(job: dict) -> Optional[str]:
    """
    Generate a personalized LinkedIn connection message for a job.
    Returns the message string (<= 290 chars) or None on skip/failure.
    """
    score = job.get("relevance_score", 0)
    if score < _OUTREACH_MIN_SCORE:
        return None

    alert_level, _ = check_budget_alert()
    if alert_level == "danger":
        return None

    title   = job.get("title", "")
    company = job.get("company", "")
    desc    = (job.get("description") or "")[:400]

    desc_context = f"Role description (first 400 chars): {desc}" if desc.strip() else "(no description)"

    prompt = (
        f"Write a LinkedIn connection request note from a job applicant to someone at a company "
        f"they're applying to. Hard limit: 290 characters total (LinkedIn connection note limit).\n\n"
        f"Candidate: {_CANDIDATE_BRIEF}\n\n"
        f"Target role: {title} at {company}\n"
        f"{desc_context}\n\n"
        f"Rules:\n"
        f"- 2-3 sentences maximum\n"
        f"- First sentence: reference ONE specific aspect of the role or company\n"
        f"- Second sentence: ONE relevant achievement of the candidate (use a number)\n"
        f"- Third sentence (optional): simple ask — 'Would love to connect'\n"
        f"- No 'I am reaching out to express my interest' or similar boilerplate\n"
        f"- No emojis, no exclamation marks, professional but direct\n"
        f"- Must be under 290 characters — count carefully\n\n"
        f"Return ONLY the message text. No quotes, no explanation."
    )

    try:
        msg = _get_client().messages.create(
            model=CLAUDE_MODEL,
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        )
        record_usage(msg.usage.input_tokens, msg.usage.output_tokens, label="outreach")
        text = msg.content[0].text.strip().strip('"').strip("'")
        # Trim to 290 chars if Claude exceeded the limit
        if len(text) > 290:
            text = text[:287].rstrip() + "..."
        logger.info("  Outreach drafted (%d chars): %s @ %s", len(text), title, company)
        return text
    except Exception as exc:
        logger.warning("Outreach generation failed for %s @ %s: %s", title, company, exc)
        return None


def add_outreach_to_jobs(jobs: list[dict]) -> None:
    """
    Generate LinkedIn outreach for every job with score >= threshold.
    Modifies jobs in-place (adds 'linkedin_outreach' key).
    """
    high_score = [j for j in jobs if (j.get("relevance_score") or 0) >= _OUTREACH_MIN_SCORE]
    if not high_score:
        return

    logger.info("── LinkedIn outreach generation (%d jobs) ───────────────────", len(high_score))
    for job in high_score:
        msg = generate_outreach(job)
        if msg:
            job["linkedin_outreach"] = msg
