"""
doc_generator.py — Career-ops HTML→PDF pipeline for internship documents.

For each compatible job:
  1. Reads cv.md, _shared.md, pdf.md, _profile.md as context
  2. Calls Claude to generate a tailored HTML resume (filling cv-template.html)
  3. Calls Claude to generate a tailored HTML cover letter (filling cover-template.html)
  4. Runs `node generate-pdf.mjs` via subprocess to render each HTML → PDF
  5. Returns (resume_pdf_path, cover_pdf_path)
"""

import json
import logging
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

import anthropic

from config import (
    ANTHROPIC_API_KEY,
    CANDIDATE,
    CLAUDE_MODEL,
    COVERS_DIR,
    RESUMES_DIR,
)
from credit_monitor import check_budget_alert, record_usage

logger = logging.getLogger(__name__)

BASE_DIR  = Path(__file__).parent
MODES_DIR = BASE_DIR / "modes"
TMPL_DIR  = BASE_DIR / "templates"
NODE_SCRIPT = BASE_DIR / "generate-pdf.mjs"

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


# ── File readers (cached per process) ────────────────────────────────────────

def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("File not found: %s", path)
        return ""


_CV_MD       = None
_SHARED_MD   = None
_PDF_MD      = None
_PROFILE_MD  = None
_CV_TMPL     = None
_COVER_TMPL  = None


def _load_context():
    global _CV_MD, _SHARED_MD, _PDF_MD, _PROFILE_MD, _CV_TMPL, _COVER_TMPL
    if _CV_MD is None:
        _CV_MD      = _read(BASE_DIR / "cv.md")
        _SHARED_MD  = _read(MODES_DIR / "_shared.md")
        _PDF_MD     = _read(MODES_DIR / "pdf.md")
        _PROFILE_MD = _read(MODES_DIR / "_profile.md")
        _CV_TMPL    = _read(TMPL_DIR / "cv-template.html")
        _COVER_TMPL = _read(TMPL_DIR / "cover-template.html")


# ── Slug helpers ──────────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    """Convert text to lowercase kebab-case slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text[:40]


def _candidate_slug() -> str:
    return _slugify(CANDIDATE["name"])


# ── Paper format detection ────────────────────────────────────────────────────

def _paper_format(job: dict) -> str:
    """Return 'letter' for US/Canada, 'a4' for everything else."""
    region = job.get("region", "")
    country = (job.get("country") or "").lower()
    if region == "USA_Canada" or country in ("us", "ca"):
        return "letter"
    return "a4"


def _page_width(fmt: str) -> str:
    return "8.5in" if fmt == "letter" else "210mm"


# ── Claude calls ──────────────────────────────────────────────────────────────

def _build_resume_prompt(job: dict) -> str:
    _load_context()
    title   = job.get("title", "")
    company = job.get("company", "")
    desc    = (job.get("description") or "")[:3000]
    fmt     = _paper_format(job)
    pw      = _page_width(fmt)

    return f"""You are generating an ATS-optimized PDF resume for an internship application.

## System context
{_SHARED_MD}

## User profile
{_PROFILE_MD}

## Candidate CV (source of truth)
{_CV_MD}

## PDF generation rules
{_PDF_MD}

---

## Task

Generate a tailored HTML resume for this job:
- **Role:** {title}
- **Company:** {company}
- **Description:** {desc if desc else "(no description available - tailor based on title and company)"}
- **Paper format:** {fmt} -> PAGE_WIDTH = {pw}

Use the HTML template below. Replace every {{PLACEHOLDER}} with the correct content.
Return ONLY the complete HTML - no markdown fences, no explanation.

Rules:
- LANG = en
- PAGE_WIDTH = {pw}
- NAME = {CANDIDATE['name']}
- PHONE = {CANDIDATE['phone']}
- EMAIL = {CANDIDATE['email']}
- LINKEDIN_URL = https://{CANDIDATE['linkedin']}
- LINKEDIN_DISPLAY = {CANDIDATE['linkedin']}
- PORTFOLIO_URL and PORTFOLIO_DISPLAY = omit both span and separator if empty
- LOCATION = {CANDIDATE['location']}
- SECTION_SUMMARY = Professional Summary
- SECTION_COMPETENCIES = Core Competencies
- SECTION_EXPERIENCE = Work Experience
- SECTION_PROJECTS = Projects
- SECTION_EDUCATION = Education
- SECTION_CERTIFICATIONS = (omit this section entirely if no certifications)
- SECTION_SKILLS = Skills
- Extract 6-8 keywords from the JD and inject them naturally into the Summary and bullets
- Select the 3-4 most relevant projects for this role
- Reorder bullets in Work Experience by relevance to the JD
- NEVER invent experience or metrics - only reformulate existing ones with JD vocabulary

## HTML Template

{_CV_TMPL}
"""


def _build_cover_prompt(job: dict) -> str:
    _load_context()
    title   = job.get("title", "")
    company = job.get("company", "")
    desc    = (job.get("description") or "")[:2000]
    fmt     = _paper_format(job)
    pw      = _page_width(fmt)
    date    = datetime.now().strftime("%B %d, %Y")

    return f"""You are generating a cover letter for an internship application.

## Candidate CV
{_CV_MD}

## User profile
{_PROFILE_MD}

---

## Task

Generate a tailored cover letter HTML for this job:
- **Role:** {title}
- **Company:** {company}
- **Description:** {desc if desc else "(no description available - tailor based on title and company)"}
- **Paper format:** {fmt} -> PAGE_WIDTH = {pw}

Use the HTML template below. Replace every {{PLACEHOLDER}} with the correct content.
Return ONLY the complete HTML - no markdown fences, no explanation.

Rules:
- LANG = en
- PAGE_WIDTH = {pw}
- NAME = {CANDIDATE['name']}
- PHONE = {CANDIDATE['phone']}
- EMAIL = {CANDIDATE['email']}
- LINKEDIN_URL = https://{CANDIDATE['linkedin']}
- LINKEDIN_DISPLAY = {CANDIDATE['linkedin']}
- LOCATION = {CANDIDATE['location']}
- DATE = {date}
- COMPANY_NAME = {company}
- HIRING_MANAGER = Hiring Team (use actual name if extractable from JD)
- SUBJECT = Application for {title} Internship
- LETTER_BODY = 3 tight paragraphs, each wrapped in <p> tags:
    Para 1: Opening hook - cite one specific detail from the JD + your strongest proof point
    Para 2: Why you are a strong fit - 2 specific achievements from your CV mapped to JD requirements
    Para 3: Why this company specifically + call to action
- Keep the letter to 1 page max
- No cliches ("passionate about", "results-oriented", "proven track record")
- Short sentences, action verbs, specific numbers

## HTML Template

{_COVER_TMPL}
"""


# ── Node PDF runner ───────────────────────────────────────────────────────────

def _run_node_pdf(html_path: Path, pdf_path: Path, fmt: str) -> bool:
    """
    Run: node generate-pdf.mjs <html> <pdf> --format=<fmt>
    Returns True on success, False on failure.
    """
    if not NODE_SCRIPT.exists():
        logger.error("generate-pdf.mjs not found at %s", NODE_SCRIPT)
        return False

    cmd = ["node", str(NODE_SCRIPT), str(html_path), str(pdf_path), f"--format={fmt}"]
    logger.info("  Running: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(BASE_DIR),
        )
        if result.returncode == 0:
            logger.info("  PDF generated: %s", pdf_path)
            for line in result.stdout.strip().splitlines():
                logger.debug("  [node] %s", line)
            return True
        else:
            logger.error("  node generate-pdf.mjs failed (rc=%d):\n%s\n%s",
                         result.returncode, result.stdout, result.stderr)
            return False
    except subprocess.TimeoutExpired:
        logger.error("  node generate-pdf.mjs timed out after 120s")
        return False
    except FileNotFoundError:
        logger.error("  'node' command not found - is Node.js installed?")
        return False


# ── Main entry point ──────────────────────────────────────────────────────────

def generate_documents(job: dict):
    """
    Generate a tailored resume PDF + cover letter PDF for a job.

    Returns:
        (resume_path, cover_path)  - str paths (empty string on failure)

    Raises:
        RuntimeError if budget is exhausted.
    """
    _load_context()

    title   = job.get("title",   "role")
    company = job.get("company", "company")
    fmt     = _paper_format(job)

    candidate_slug = _candidate_slug()
    company_slug   = _slugify(company)
    date_str       = datetime.now().strftime("%Y-%m-%d")

    # Create tmp dir for intermediate HTML files
    tmp_dir = BASE_DIR / "tmp"
    tmp_dir.mkdir(exist_ok=True)

    logger.info("Generating docs for: %s @ %s", title, company)

    # ── Budget gate ───────────────────────────────────────────────────────────
    alert_level, alert_msg = check_budget_alert()
    if alert_level == "danger":
        raise RuntimeError(alert_msg)

    resume_path = ""
    cover_path  = ""

    # ── 1. Generate resume HTML ───────────────────────────────────────────────
    try:
        resume_prompt = _build_resume_prompt(job)
        msg = _get_client().messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4096,
            messages=[{"role": "user", "content": resume_prompt}],
        )
        record_usage(msg.usage.input_tokens, msg.usage.output_tokens, label="resume")

        resume_html = msg.content[0].text.strip()
        # Strip any accidental markdown fences
        if resume_html.startswith("```"):
            resume_html = re.sub(r"^```[^\n]*\n", "", resume_html)
            resume_html = re.sub(r"\n```$", "", resume_html.rstrip())

        html_file = tmp_dir / f"cv-{candidate_slug}-{company_slug}.html"
        html_file.write_text(resume_html, encoding="utf-8")

        pdf_file = RESUMES_DIR / f"cv-{candidate_slug}-{company_slug}-{date_str}.pdf"
        if _run_node_pdf(html_file, pdf_file, fmt):
            resume_path = str(pdf_file)
        else:
            # Keep HTML as fallback so the document isn't lost
            fallback = RESUMES_DIR / f"cv-{candidate_slug}-{company_slug}-{date_str}.html"
            import shutil
            shutil.copy(str(html_file), str(fallback))
            resume_path = str(fallback)
            logger.warning("  PDF failed - saved HTML fallback: %s", fallback)

    except Exception as exc:
        logger.error("Resume generation failed for %s @ %s: %s", title, company, exc)

    # ── 2. Generate cover letter HTML ─────────────────────────────────────────
    try:
        cover_prompt = _build_cover_prompt(job)
        msg = _get_client().messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": cover_prompt}],
        )
        record_usage(msg.usage.input_tokens, msg.usage.output_tokens, label="cover")

        cover_html = msg.content[0].text.strip()
        if cover_html.startswith("```"):
            cover_html = re.sub(r"^```[^\n]*\n", "", cover_html)
            cover_html = re.sub(r"\n```$", "", cover_html.rstrip())

        html_file = tmp_dir / f"cover-{candidate_slug}-{company_slug}.html"
        html_file.write_text(cover_html, encoding="utf-8")

        pdf_file = COVERS_DIR / f"cover-{candidate_slug}-{company_slug}-{date_str}.pdf"
        if _run_node_pdf(html_file, pdf_file, fmt):
            cover_path = str(pdf_file)
        else:
            fallback = COVERS_DIR / f"cover-{candidate_slug}-{company_slug}-{date_str}.html"
            import shutil
            shutil.copy(str(html_file), str(fallback))
            cover_path = str(fallback)
            logger.warning("  PDF failed - saved HTML fallback: %s", fallback)

    except Exception as exc:
        logger.error("Cover letter generation failed for %s @ %s: %s", title, company, exc)

    return resume_path, cover_path
