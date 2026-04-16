"""
doc_generator.py — Career-ops HTML→PDF pipeline for internship documents.

Two-step approach per document:
  1. Claude call  → returns structured JSON (content only, no HTML)
  2. Python fills the HTML template with that JSON content
  3. node generate-pdf.mjs renders HTML → PDF

This keeps template design fixed and lets Claude focus purely on
writing content that is customised for each job offer.
"""

import json
import logging
import re
import subprocess
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

BASE_DIR    = Path(__file__).parent
MODES_DIR   = BASE_DIR / "modes"
TMPL_DIR    = BASE_DIR / "templates"
NODE_SCRIPT = BASE_DIR / "generate-pdf.mjs"

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


# ── Cached file reads ─────────────────────────────────────────────────────────

_cache = {}

def _read(path: Path) -> str:
    if path not in _cache:
        try:
            _cache[path] = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.warning("File not found: %s", path)
            _cache[path] = ""
    return _cache[path]

def _cv_md():      return _read(BASE_DIR / "cv.md")
def _profile_md(): return _read(MODES_DIR / "_profile.md")
def _shared_md():  return _read(MODES_DIR / "_shared.md")
def _cv_tmpl():    return _read(TMPL_DIR / "cv-template.html")
def _cl_tmpl():    return _read(TMPL_DIR / "cover-template.html")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text[:40]

def _paper_format(job: dict) -> str:
    if job.get("region") == "USA_Canada" or (job.get("country") or "").lower() in ("us", "ca"):
        return "letter"
    return "a4"

def _page_width(fmt: str) -> str:
    return "8.5in" if fmt == "letter" else "210mm"

def _parse_json(text: str) -> dict:
    """Extract JSON from Claude response, stripping any markdown fences."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


# ── Claude: generate resume content ──────────────────────────────────────────

_RESUME_SCHEMA = """{
  "title": "short role descriptor | skill1 · skill2 · skill3",
  "summary": "3-4 sentence paragraph, keyword-dense, first-person omitted, action verbs",
  "competencies": ["tag1", "tag2", "tag3", "tag4", "tag5", "tag6", "tag7", "tag8"],
  "experience": [
    {
      "company": "Company Name",
      "role": "Job Title",
      "period": "Mon YYYY – Mon YYYY",
      "location": "City, Country",
      "bullets": [
        "Lead bullet with <strong>metric or keyword</strong> bolded",
        "Second bullet",
        "Third bullet",
        "Fourth bullet"
      ]
    }
  ],
  "projects": [
    {
      "name": "Project Name",
      "badge": "Context · Year · Achievement",
      "bullets": [
        "Lead bullet with <strong>key result</strong> bolded",
        "Second bullet with tech detail"
      ],
      "tech": "Tech1 · Tech2 · Tech3"
    }
  ],
  "education": [
    {
      "degree": "Degree Name",
      "school": "School Name",
      "period": "Sep YYYY – Present",
      "location": "City, Country",
      "desc": "Relevant coursework: ..."
    }
  ],
  "skills": [
    {"category": "Category Name", "items": "skill1 · skill2 · skill3"},
    {"category": "Category Name", "items": "skill1 · skill2"}
  ]
}"""

def _call_resume_content(job: dict) -> dict:
    title   = job.get("title", "")
    company = job.get("company", "")
    desc    = (job.get("description") or "")[:3000]

    prompt = f"""You are writing a highly targeted resume for one specific internship application.
Your goal: make the hiring manager feel this resume was written FOR this exact role.

## Candidate CV (source of truth — never invent facts, only reformulate with JD vocabulary)
{_cv_md()}

## Candidate profile & target roles
{_profile_md()}

## Target role
Company: {company}
Role: {title}
Job description:
{desc if desc else "(no description — infer from role title and company)"}

## Instructions — follow every rule exactly

### 1. JD keyword extraction (do this first)
Identify 8-10 specific technical terms, tools, and methods from this JD.
Examples: "transformer fine-tuning", "production ML systems", "A/B testing", "time-series forecasting".
Every one of these MUST appear naturally in the summary, bullets, or competencies.

### 2. Title field
Write a role descriptor that mirrors the JD language exactly.
Example for an NLP role at {company}: "NLP Engineer | Transformer Fine-Tuning · Text Classification · LLM Deployment"
NOT generic — specific to this company and role.

### 3. Summary (3-4 sentences)
- Sentence 1: lead with your strongest proof point that directly maps to the top JD requirement
- Sentence 2-3: two more specific achievements using JD vocabulary
- Sentence 4: why {company} specifically (one genuine reason from the JD context)
No filler phrases ("passionate about", "team player", "results-oriented").

### 4. Experience bullets
- Reorder ALL bullets within each role so the most JD-relevant one is first
- Rewrite bullets to use exact JD terminology where truthful
- Bold (<strong>) the single most impressive metric or JD keyword in each bullet
- At least 2 bullets per role must directly reference skills/tools mentioned in the JD

### 5. Projects
- Select the 3 projects most directly relevant to THIS role (not generic relevance)
- Rewrite the lead bullet of each project to emphasize the aspect most relevant to the JD
- If the JD mentions a specific domain (CV, NLP, tabular data, etc.), front-load that project

### 6. Competencies
- 8 tags taken VERBATIM from the JD requirements section
- These must match what the recruiter's ATS will scan for

### 7. Hard rules
- NEVER invent metrics, tools, or experience not in the CV
- No em-dashes (use -), no smart quotes, action verbs, short sentences
- Every section must feel written for {company} specifically, not recycled

## JSON Schema
{_RESUME_SCHEMA}

Return ONLY valid JSON. No markdown fences, no explanation.
"""

    msg = _get_client().messages.create(
        model=CLAUDE_MODEL,
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}],
    )
    record_usage(msg.usage.input_tokens, msg.usage.output_tokens, label="resume")
    return _parse_json(msg.content[0].text)


# ── Claude: generate cover letter content ────────────────────────────────────

_COVER_SCHEMA = """{
  "title": "short role descriptor | skill1 · skill2 · skill3",
  "doc_metadata": "Firstname Lastname · Role Application · Company · Season YYYY",
  "recipient_role": "Hiring Team — exact role title from JD",
  "company_full": "Full Company Name · City, Country",
  "salutation": "Dear [Team/Name],",
  "paragraphs": [
    "<p>Opening hook: cite one specific JD detail + your strongest proof point. Bold 2-3 key terms with <strong>.</strong></p>",
    "<p>Two concrete achievements mapped to JD requirements. Bold metrics and tech with <strong>.</strong></p>",
    "<p>Why this company specifically + call to action. Keep it direct, no fluff.</p>"
  ]
}"""

def _call_cover_content(job: dict) -> dict:
    title   = job.get("title", "")
    company = job.get("company", "")
    desc    = (job.get("description") or "")[:2000]
    date    = datetime.now().strftime("%B %d, %Y")

    prompt = f"""You are writing a cover letter for one specific internship application.
Make it feel like it could only have been written for {company}'s {title} role.

## Candidate CV (source of truth)
{_cv_md()}

## Candidate profile
{_profile_md()}

## Target role
Company: {company}
Role: {title}
Date: {date}
Description:
{desc if desc else "(no description — infer from role title and company context)"}

## Instructions

### Paragraph 1 — Hook (under 80 words)
- Open with a single, specific JD requirement pulled verbatim from the description
- Immediately map it to your single strongest proof point (e.g. "#1 on EURECOM leaderboard",
  "100K-record ML pipeline")
- Bold 2-3 key terms with <strong>
- Do NOT start with "I am" or "I would like"

### Paragraph 2 — Proof (under 100 words)
- Two concrete achievements with exact numbers, directly mapped to two more JD requirements
- Use the exact technical terms from the JD
- Bold every metric and tool name with <strong>
- These must be different proof points from paragraph 1

### Paragraph 3 — Why {company} (under 60 words)
- One specific, researched reason you want to work at {company} (from JD context, company name, sector)
- Direct call to action — one sentence
- No cliches, no "excited opportunity", no "passionate about"

## Hard rules
- "paragraphs" must be complete HTML strings with <p> tags
- No em-dashes (use -), no smart quotes
- "doc_metadata": "Aymane Ait Dads · {title} Application · {company} · Summer 2026"
- Max 3 paragraphs total

## JSON Schema
{_COVER_SCHEMA}

Return ONLY valid JSON. No markdown fences, no explanation.
"""

    msg = _get_client().messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    record_usage(msg.usage.input_tokens, msg.usage.output_tokens, label="cover")
    return _parse_json(msg.content[0].text)


# ── Template filling ──────────────────────────────────────────────────────────

def _fill_resume_html(data: dict, fmt: str) -> str:
    # Build HTML blocks from JSON data
    competencies = "".join(
        f'<span class="competency-tag">{c}</span>'
        for c in data.get("competencies", [])
    )

    experience_html = ""
    for job in data.get("experience", []):
        bullets = "".join(f"<li>{b}</li>" for b in job.get("bullets", []))
        experience_html += f"""
    <div class="job">
      <div class="job-header">
        <span class="job-company">{job.get("company","")}</span>
        <span class="job-period">{job.get("period","")}</span>
      </div>
      <div class="job-role">{job.get("role","")} <span class="job-location">— {job.get("location","")}</span></div>
      <ul>{bullets}</ul>
    </div>"""

    projects_html = ""
    for p in data.get("projects", []):
        bullets = "".join(f"<li>{b}</li>" for b in p.get("bullets", []))
        tech = f'<div class="project-tech">{p["tech"]}</div>' if p.get("tech") else ""
        projects_html += f"""
    <div class="project avoid-break">
      <div class="project-header">
        <span class="project-title">{p.get("name","")}</span>
        <span class="project-badge">{p.get("badge","")}</span>
      </div>
      <ul>{bullets}</ul>
      {tech}
    </div>"""

    education_html = ""
    for e in data.get("education", []):
        desc = f'<div class="edu-desc">{e["desc"]}</div>' if e.get("desc") else ""
        education_html += f"""
    <div class="edu-item">
      <div class="edu-header">
        <div>
          <span class="edu-degree">{e.get("degree","")}</span>
          <span> — </span>
          <span class="edu-school">{e.get("school","")}</span>
        </div>
        <span class="edu-year">{e.get("period","")}</span>
      </div>
      {desc}
    </div>"""

    skills_html = ""
    for s in data.get("skills", []):
        skills_html += f'<div class="skill-row"><span class="skill-category">{s.get("category","")}: </span>{s.get("items","")}</div>'

    tmpl = _cv_tmpl()
    replacements = {
        "{{LANG}}":             "en",
        "{{PAGE_WIDTH}}":       _page_width(fmt),
        "{{NAME}}":             CANDIDATE["name"],
        "{{TITLE}}":            data.get("title", CANDIDATE["title"]),
        "{{EMAIL}}":            CANDIDATE["email"],
        "{{PHONE}}":            CANDIDATE["phone"],
        "{{LINKEDIN_URL}}":     f"https://{CANDIDATE['linkedin']}",
        "{{LINKEDIN_DISPLAY}}": CANDIDATE["linkedin"],
        "{{LOCATION}}":         CANDIDATE["location"],
        "{{INSTITUTION}}":      CANDIDATE["school"],
        "{{DEGREE}}":           CANDIDATE["degree"],
        "{{INST_LOCATION}}":    CANDIDATE["location"],
        "{{SUMMARY}}":          data.get("summary", ""),
        "{{COMPETENCIES}}":     competencies,
        "{{EXPERIENCE}}":       experience_html,
        "{{PROJECTS}}":         projects_html,
        "{{EDUCATION}}":        education_html,
        "{{SKILLS}}":           skills_html,
    }
    for key, val in replacements.items():
        tmpl = tmpl.replace(key, val)
    return tmpl


def _fill_cover_html(data: dict, fmt: str) -> str:
    letter_body = "".join(data.get("paragraphs", []))
    date_str    = datetime.now().strftime("%B %d, %Y")

    tmpl = _cl_tmpl()
    replacements = {
        "{{LANG}}":             "en",
        "{{PAGE_WIDTH}}":       _page_width(fmt),
        "{{NAME}}":             CANDIDATE["name"],
        "{{TITLE}}":            data.get("title", CANDIDATE["title"]),
        "{{EMAIL}}":            CANDIDATE["email"],
        "{{PHONE}}":            CANDIDATE["phone"],
        "{{LINKEDIN_URL}}":     f"https://{CANDIDATE['linkedin']}",
        "{{LINKEDIN_DISPLAY}}": CANDIDATE["linkedin"],
        "{{LOCATION}}":         CANDIDATE["location"],
        "{{INSTITUTION}}":      CANDIDATE["school"],
        "{{DEGREE}}":           CANDIDATE["degree"],
        "{{INST_LOCATION}}":    CANDIDATE["location"],
        "{{DOC_METADATA}}":     data.get("doc_metadata", ""),
        "{{DATE}}":             date_str,
        "{{RECIPIENT_ROLE}}":   data.get("recipient_role", "Hiring Team"),
        "{{COMPANY_FULL}}":     data.get("company_full", ""),
        "{{SALUTATION}}":       data.get("salutation", "Dear Hiring Team,"),
        "{{LETTER_BODY}}":      letter_body,
    }
    for key, val in replacements.items():
        tmpl = tmpl.replace(key, val)
    return tmpl


# ── Node PDF runner ───────────────────────────────────────────────────────────

def _run_node_pdf(html_path: Path, pdf_path: Path, fmt: str) -> bool:
    if not NODE_SCRIPT.exists():
        logger.error("generate-pdf.mjs not found at %s", NODE_SCRIPT)
        return False
    cmd = ["node", str(NODE_SCRIPT), str(html_path), str(pdf_path), f"--format={fmt}"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=str(BASE_DIR))
        if result.returncode == 0:
            logger.info("  PDF generated: %s", pdf_path.name)
            return True
        logger.error("  node failed (rc=%d): %s", result.returncode, result.stderr[:300])
        return False
    except subprocess.TimeoutExpired:
        logger.error("  node timed out")
        return False
    except FileNotFoundError:
        logger.error("  'node' not found - is Node.js installed?")
        return False


# ── Main entry point ──────────────────────────────────────────────────────────

def generate_documents(job: dict):
    """
    Generate resume PDF + cover letter PDF for a job.
    Returns (resume_path, cover_path) — empty string on failure.
    Raises RuntimeError if budget is exhausted.
    """
    title   = job.get("title", "role")
    company = job.get("company", "company")
    fmt     = _paper_format(job)

    cand_slug = _slugify(CANDIDATE["name"])
    co_slug   = _slugify(company)
    date_str  = datetime.now().strftime("%Y-%m-%d")

    tmp_dir = BASE_DIR / "tmp"
    tmp_dir.mkdir(exist_ok=True)

    logger.info("Generating docs: %s @ %s", title, company)

    alert_level, alert_msg = check_budget_alert()
    if alert_level == "danger":
        raise RuntimeError(alert_msg)

    resume_path = ""
    cover_path  = ""

    # ── Resume ────────────────────────────────────────────────────────────────
    try:
        resume_data = _call_resume_content(job)
        html        = _fill_resume_html(resume_data, fmt)
        html_file   = tmp_dir / f"cv-{cand_slug}-{co_slug}.html"
        html_file.write_text(html, encoding="utf-8")
        pdf_file = RESUMES_DIR / f"cv-{cand_slug}-{co_slug}-{date_str}.pdf"
        if _run_node_pdf(html_file, pdf_file, fmt):
            resume_path = str(pdf_file)
        else:
            # Keep HTML as fallback
            fb = RESUMES_DIR / f"cv-{cand_slug}-{co_slug}-{date_str}.html"
            import shutil; shutil.copy(str(html_file), str(fb))
            resume_path = str(fb)
    except Exception as exc:
        logger.error("Resume failed for %s @ %s: %s", title, company, exc)

    # ── Cover letter ──────────────────────────────────────────────────────────
    try:
        cover_data = _call_cover_content(job)
        html       = _fill_cover_html(cover_data, fmt)
        html_file  = tmp_dir / f"cover-{cand_slug}-{co_slug}.html"
        html_file.write_text(html, encoding="utf-8")
        pdf_file = COVERS_DIR / f"cover-{cand_slug}-{co_slug}-{date_str}.pdf"
        if _run_node_pdf(html_file, pdf_file, fmt):
            cover_path = str(pdf_file)
        else:
            fb = COVERS_DIR / f"cover-{cand_slug}-{co_slug}-{date_str}.html"
            import shutil; shutil.copy(str(html_file), str(fb))
            cover_path = str(fb)
    except Exception as exc:
        logger.error("Cover letter failed for %s @ %s: %s", title, company, exc)

    return resume_path, cover_path
