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
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple

import anthropic
import requests

from config import ANTHROPIC_API_KEY, CANDIDATE, CLAUDE_MODEL, MIN_RELEVANCE_SCORE
from credit_monitor import check_budget_alert, record_usage

logger = logging.getLogger(__name__)

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


# ── URL health check ─────────────────────────────────────────────────────────

_URL_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}

# Patterns in the FINAL URL (after redirects) that signal a dead listing.
# Job boards redirect expired jobs to their search/home page.
_DEAD_URL_PATTERNS = [
    # Indeed: expired job redirects to search page
    "indeed.com/jobs",
    "indeed.com/?",
    "indeed.com/q-",
    # Glassdoor: expired job redirects to listings page
    "glassdoor.com/job-listing/expired",
    "glassdoor.com/Job/jobs",
    # Generic expiry paths
    "/expired",
    "/job-expired",
    "/not-found",
    "/404",
    "no-longer-available",
    "position-closed",
    "job-closed",
]

# Body text patterns that appear in raw HTML (non-JS-rendered pages)
_DEAD_BODY_PATTERNS = [
    "job is no longer available",
    "this job has expired",
    "job has expired",
    "position has been filled",
    "application is closed",
    "no longer accepting applications",
    "job listing is expired",
    "this position is no longer available",
    "job has been removed",
    "listing has expired",
    "this listing has been removed",
    "vacancy is closed",
    "this role has been filled",
    # Multi-language
    "diese stelle ist nicht mehr verfügbar",   # German
    "ce poste n'est plus disponible",           # French
    "este puesto ya no está disponible",        # Spanish
    "esta vaga não está mais disponível",       # Portuguese
]

# Sources where URL checking is unreliable (always redirect to login)
_SKIP_URL_CHECK_SOURCES = {"LinkedIn"}


def _is_url_alive(job: dict, timeout: int = 8) -> bool:
    """
    Return False only when we're certain the listing is dead.
    Strategy:
      1. Skip LinkedIn — always redirects to login (200 but not a dead listing)
      2. HTTP 404 / 410 → dead
      3. Check final URL after redirects against known dead-redirect patterns
      4. Check raw body for known expiry strings (catches Indeed, Glassdoor raw pages)
    On timeout or any error → assume alive (never silently drop).
    """
    url    = job.get("url", "")
    source = job.get("source", "")

    if not url:
        return True
    if source in _SKIP_URL_CHECK_SOURCES:
        return True  # can't reliably check LinkedIn without auth

    try:
        r = requests.get(
            url, timeout=timeout, headers=_URL_HEADERS,
            allow_redirects=True,
        )
        final_url = r.url.lower()

        if r.status_code in (404, 410):
            return False

        # Check if we were redirected to a generic search/home page
        if any(p in final_url for p in _DEAD_URL_PATTERNS):
            return False

        # Check raw body (works for non-JS pages)
        if r.status_code == 200:
            body = r.text.lower()
            if any(p in body for p in _DEAD_BODY_PATTERNS):
                return False

        return True
    except Exception:
        return True  # timeout / connection error → assume alive


def _filter_dead_urls(jobs: List[dict]) -> Tuple[List[dict], int]:
    """
    Check all job URLs in parallel (10 workers).
    Returns (alive_jobs, dead_count).
    """
    if not jobs:
        return jobs, 0

    logger.info("URL health check: testing %d links ...", len(jobs))
    alive: List[dict] = []
    dead_count = 0

    with ThreadPoolExecutor(max_workers=10) as pool:
        future_to_job = {
            pool.submit(_is_url_alive, j): j for j in jobs
        }
        for future in as_completed(future_to_job):
            job = future_to_job[future]
            if future.result():
                alive.append(job)
            else:
                dead_count += 1
                logger.info(
                    "  ✗ Dead URL: %-40s @ %s",
                    (job.get("title") or "")[:40],
                    (job.get("company") or "")[:25],
                )

    if dead_count:
        logger.info("URL health check: removed %d dead listings", dead_count)
    return alive, dead_count


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
    # Senior / permanent roles — not internships
    "senior ", "sr.", "sr ", "lead ", "principal ", "staff ", "director",
    "head of", "vp ", "vice president", "chief ", "manager,", "manager ",
    " ftc", "permanent", "full-time permanent", "12 month ftc", "fixed term",
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
            f"Rate this role for the candidate (1-10).\n"
            f"IMPORTANT RULES:\n"
            f"- Score 1 if the role is senior/permanent (senior, lead, principal, staff, "
            f"director, manager, FTC, permanent, full-time) — candidate is a student seeking internships only.\n"
            f"- Score 1 if the role is unrelated to data/ML/AI/tech.\n"
            f"Job title: {title} at {company}\n\n"
            f"Score: 8-10 = DS/ML/AI intern or entry-level fit, 5-7 = adjacent/ambiguous, "
            f"1-4 = senior/permanent role OR clearly unrelated.\n"
            f'Reply JSON only: {{"score": <1-10>, "reason": "<max 12 words>"}}'
        )
    else:
        prompt = (
            f"{_CANDIDATE_SNAPSHOT}\n\n"
            f"Rate this role for the candidate (1-10).\n"
            f"IMPORTANT RULES:\n"
            f"- Score 1 if the role is senior/permanent (senior, lead, principal, staff, "
            f"director, manager, FTC, permanent, full-time) — candidate is a student seeking internships only.\n"
            f"- Score 1 if the role is unrelated to data/ML/AI/tech.\n"
            f"Job: {title} at {company}\nDescription: {desc}\n\n"
            f"8-10 = DS/ML/AI intern/entry-level strong fit, 5-7 = data-adjacent intern, "
            f"1-4 = senior/permanent role OR unrelated.\n"
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

    # ── Step 0: URL health check (remove dead/expired listings) ──────────────
    jobs, dead_count = _filter_dead_urls(jobs)
    if not jobs:
        logger.info("All jobs had dead URLs — nothing to score.")
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
