"""
job_filter.py — Relevance scoring for internship offers.

Strategy (in order):
  1. Title keyword pre-check (free, instant):
       - Clear DS/ML/AI title  → score 8, skip Claude
       - Clearly unrelated     → score 2, skip Claude
  2. Claude scoring for ambiguous titles:
       - Uses description if available, title alone if not
       - On any failure → score 8 (include, never silently drop)
"""

import json
import logging
from typing import List, Tuple

import anthropic

from config import ANTHROPIC_API_KEY, CANDIDATE, CLAUDE_MODEL, MIN_RELEVANCE_SCORE
from credit_monitor import check_budget_alert, record_usage

logger = logging.getLogger(__name__)

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


# ── Title keyword lists ───────────────────────────────────────────────────────

_INCLUDE_KEYWORDS = [
    "data science", "data scientist", "machine learning", "deep learning",
    "artificial intelligence", " ai ", "ai/ml", "nlp", "natural language",
    "computer vision", "data engineer", "data analyst", "ml engineer",
    "research scientist", "applied scientist", "research intern",
    "data mining", "predictive", "analytics intern", "llm",
    "transformer", "reinforcement learning", "mlops", "data intern",
    "quantitative", "statistician",
]

_EXCLUDE_KEYWORDS = [
    "marketing", "sales representative", "finance intern", "accounting",
    "legal intern", "hr intern", "human resources", "customer service",
    "content writer", "social media", "graphic design", "ux designer",
    "ui designer", "product manager", "project manager", "supply chain",
    "logistics", "administrative", "receptionist", "retail", "copywriter",
    "brand", "events intern", "recruiter", "talent acquisition",
    "business development", "operations intern",
]


def _title_prescreens(title: str) -> Tuple[bool, bool]:
    """
    Returns (is_clear_match, is_clear_reject) based on job title alone.
    At most one can be True.
    """
    t = title.lower()
    clear_match  = any(k in t for k in _INCLUDE_KEYWORDS)
    clear_reject = any(k in t for k in _EXCLUDE_KEYWORDS) and not clear_match
    return clear_match, clear_reject


# Compact candidate snapshot — kept short to minimise token usage
_CANDIDATE_SNAPSHOT = (
    f"Candidate: {CANDIDATE['degree']} at {CANDIDATE['school']}. "
    f"Experience: {CANDIDATE['experience'][:260]}. "
    f"Key skills: Python, PyTorch, TensorFlow, Transformers/CLIP fine-tuning, "
    f"ML pipelines, NLP, Computer Vision, Data Science, SQL, Pandas, Scikit-learn."
)


def score_job(job: dict) -> Tuple[int, str]:
    """
    Score a single job 1–10 for compatibility.
    Returns (score, reason).
    Never returns a score that silently drops a job on error — defaults to 8.
    """
    title   = job.get("title",   "") or ""
    company = job.get("company", "") or ""
    desc    = (job.get("description") or "")[:500]

    # ── Step 1: fast title pre-screen (no API call) ───────────────────────────
    clear_match, clear_reject = _title_prescreens(title)

    if clear_match:
        logger.debug("  title-match '%s' → auto 8", title)
        return 8, "title clearly matches DS/ML/AI internship"

    if clear_reject:
        logger.debug("  title-reject '%s' → auto 2", title)
        return 2, "title indicates unrelated field"

    # ── Step 2: Claude scoring for ambiguous titles ───────────────────────────
    # Budget gate
    alert_level, _ = check_budget_alert()
    if alert_level == "danger":
        return 8, "budget exhausted — included by default"

    # If description is empty, evaluate on title alone with a lenient prompt
    if len(desc.strip()) < 60:
        prompt = (
            f"{_CANDIDATE_SNAPSHOT}\n\n"
            f"Rate this internship for the candidate (1-10). "
            f"Only the job title is available — be generous if the title is plausibly "
            f"data/tech related.\n"
            f"Job title: {title} at {company}\n\n"
            f"Score: 8-10 = DS/ML/AI/tech role, 5-7 = adjacent/ambiguous, "
            f"1-4 = clearly unrelated.\n"
            f'Reply JSON only: {{"score": <1-10>, "reason": "<max 12 words>"}}'
        )
    else:
        prompt = (
            f"{_CANDIDATE_SNAPSHOT}\n\n"
            f"Rate this internship for the candidate (1-10).\n"
            f"Job: {title} at {company}\nDescription: {desc}\n\n"
            f"8-10 = strong DS/ML/AI fit, 5-7 = data-adjacent, 1-4 = unrelated.\n"
            f'Reply JSON only: {{"score": <1-10>, "reason": "<max 12 words>"}}'
        )

    try:
        msg = _get_client().messages.create(
            model=CLAUDE_MODEL,
            max_tokens=60,
            messages=[{"role": "user", "content": prompt}],
        )
        record_usage(msg.usage.input_tokens, msg.usage.output_tokens, label="filter")

        raw = msg.content[0].text.strip().strip("`")
        if raw.startswith("json"):
            raw = raw[4:].strip()

        data   = json.loads(raw)
        score  = max(1, min(10, int(data.get("score", 8))))
        reason = str(data.get("reason", ""))[:120]
        return score, reason

    except Exception as exc:
        logger.warning("Score failed for '%s @ %s': %s — including by default", title, company, exc)
        return 8, "scoring failed — included by default"


def filter_jobs(
    jobs: List[dict],
    min_score: int = MIN_RELEVANCE_SCORE,
) -> Tuple[List[dict], List[dict]]:
    """
    Score every job and split into compatible vs rejected.

    Returns:
        compatible  — jobs with score >= min_score
        rejected    — jobs below threshold
    """
    if not jobs:
        return [], []

    logger.info("── Relevance filter (min score: %d/10) ─────────────────────", min_score)

    compatible: List[dict] = []
    rejected:   List[dict] = []

    for job in jobs:
        score, reason = score_job(job)
        job["relevance_score"]  = score
        job["relevance_reason"] = reason

        icon = "✓" if score >= min_score else "✗"
        logger.info(
            "  %s [%2d/10]  %-40s @ %-25s  %s",
            icon, score, (job.get("title") or "")[:40],
            (job.get("company") or "")[:25], reason,
        )

        if score >= min_score:
            compatible.append(job)
        else:
            rejected.append(job)

    logger.info(
        "Relevance filter done: %d compatible / %d rejected (threshold %d/10)",
        len(compatible), len(rejected), min_score,
    )
    return compatible, rejected
