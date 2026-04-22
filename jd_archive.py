"""
jd_archive.py — Save and retrieve full job description text.

Why: Job listings are removed days after posting. Archiving the JD at scrape time
means /regenerate and skills-gap analysis always have the original text available.

Storage: data/jd_archive/{job_id}.txt  (plain UTF-8, one file per job)
"""

import logging
from pathlib import Path

from config import DATA_DIR

logger = logging.getLogger(__name__)

ARCHIVE_DIR = DATA_DIR / "jd_archive"


def save_jd(job_id: str, description: str) -> bool:
    """Write description to data/jd_archive/{job_id}.txt. Returns True on success."""
    if not job_id or not description:
        return False
    try:
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        path = ARCHIVE_DIR / f"{job_id}.txt"
        path.write_text(description.strip(), encoding="utf-8")
        return True
    except Exception as exc:
        logger.warning("save_jd failed for %s: %s", job_id, exc)
        return False


def load_jd(job_id: str) -> str:
    """Return archived description for job_id, or empty string if not found."""
    if not job_id:
        return ""
    try:
        path = ARCHIVE_DIR / f"{job_id}.txt"
        return path.read_text(encoding="utf-8") if path.exists() else ""
    except Exception as exc:
        logger.warning("load_jd failed for %s: %s", job_id, exc)
        return ""


def archive_jobs(jobs: list) -> int:
    """Bulk-archive a list of job dicts. Returns number of JDs saved."""
    saved = 0
    for job in jobs:
        job_id = job.get("job_id", "")
        desc   = job.get("description", "")
        if save_jd(job_id, desc):
            saved += 1
    return saved


def jd_exists(job_id: str) -> bool:
    return (ARCHIVE_DIR / f"{job_id}.txt").exists() if job_id else False
