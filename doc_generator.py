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

_CANDIDATE_FULL = """
IDENTITY
Name: Aymane Ait Dads | Email: Aymane.Ait-dads@eurecom.fr | Phone: +33 7 60 92 50 93
LinkedIn: linkedin.com/in/aymane-ait-dads | Location: EURECOM, Sophia Antipolis, France

EDUCATION
- EURECOM, Sophia Antipolis, France — Engineering Degree in Data Science | Sep 2023 – Present
  Master-level engineering program. Courses: Machine Learning, Deep Learning, Statistics,
  Image Security, Cloud Computing, Web Applications, Computer Vision, NLP.
- Ibn Timiya, Marrakech, Morocco — CPGE Mathematics & Physics | 2021 – 2023

WORK EXPERIENCE
- Orange Maroc, Casablanca | Data Science Intern | Jul – Aug 2024
  * Built Random Forest + K-Means models on 100K+ fiber-optic network records (upload speed,
    latency, jitter) to classify network quality across thousands of nodes
  * Developed clustering pipeline mapping customer performance profiles to 5 commercial service
    tiers; output used directly by the network optimization team
  * Reduced manual analysis time by ~60% via automated feature engineering and model evaluation
  * Delivered stakeholder presentations translating model outputs into maintenance recommendations
  * Stack: Python, Scikit-learn, Pandas, Power BI

PROJECTS (use these facts exactly — never invent)

PROJECT 0: NVIDIA Nemotron Model Reasoning Challenge — Kaggle (In Progress, June 2026)
  HEADLINE: Ranked 17th / 3,000+ teams · score 0.86/1.0 · $106K+ prize pool
  Base model: metric/nemotron-3-nano-30b-a3b-bf16 (30B parameters, BF16)
  Task: 5 structured reasoning categories — unit_conversion (3170), bit_manipulation (1602),
        gravity (1597), cipher (1576), symbolic (1555) — output must be inside \boxed{final answer}.
        Each category requires a fundamentally different reasoning strategy; cipher requires
        learning a substitution mapping, bit_manipulation requires discovering binary transformation
        rules, gravity requires physics-style computation, symbolic requires algebraic reasoning.

  TRAINING PIPELINE (what I built):
  - LoRA SFT using Unsloth/SFTTrainer, BF16, completion-only loss, no packing
  - Context length: 8,192 tokens (required for long chain-of-thought reasoning)
  - LoRA: r=32, alpha=32, dropout=0.0, targeting ALL attention projections + MLP + lm_head:
      [q_proj, k_proj, v_proj, o_proj, in_proj, out_proj, up_proj, down_proj, lm_head]
  - Hyperparameters: 1 epoch, batch=1, grad_accum=32, LR=3e-4, linear scheduler, beta2=0.95
  - First strong run: 0.85 leaderboard score

  STAGED CONTINUATION TRAINING (key innovation):
  - Loaded base model + 0.85 adapter, continued training on same verified CoT data
  - Second stage improved 0.85 → 0.86 — showing the initial adapter had not fully converged
  - Tested continuation from 0.86 with multiple LRs: LR=3e-4 degraded (overtraining),
    LR=1e-4 and LR=3e-5 preserved 0.86 but did not improve — local plateau reached
  - Key result: staged fine-tuning with controlled LR is a valid strategy for pushing LoRA adapters

  ABLATION EXPERIMENTS (what I discovered doesn't work):
  - Synthetic augmentation (4,929 verified CoT rows + 0 boxed mismatches): score dropped 0.86→0.77
    Root cause: distribution/formatting mismatch, not data volume — confirmed that reasoning
    style consistency matters more than data quantity in LLM SFT
  - Compact-hard training (70 curated cipher + bit_manipulation rows): score dropped to 0.69
    Likely caused by completion-template mismatch in modified pipeline, not the hard-task data itself

  TASK-LEVEL ERROR ANALYSIS (original diagnostic work):
  - Built diagnostic notebook: loaded 0.86 adapter + base model, ran eval on individual task rows
  - Discovery: model fails on cipher/bit_manipulation NOT because of wrong reasoning,
    but because it over-expands: lists full substitution mappings, dumps bit tables, uses
    entire token budget, and never reaches \boxed{}
  - Numeral tasks: model produces correct boxed answer
  - Cipher tasks: model begins substitution correctly, then lists too many character mappings,
    runs out of space, never finishes
  - Bit manipulation: model starts listing output/input bit columns, never reaches final answer
  - Key insight: hard-task failure = reasoning compactness problem, not correctness problem

  CUSTOM VERIFICATION PIPELINES (engineering work):
  - Cipher parser: reconstructed character-level substitution mappings from examples,
    kept only rows where decoded target exactly matched ground truth → 605 verified cipher rows
  - 8-bit operation solver: conservative solver over XOR, AND, OR, shifts, rotations —
    kept only examples where exactly ONE operation matched all given examples → 64 verified rows
    (rejected 1,452 as no_matching_simple_op, 86 as ambiguous)
  - Synthetic dataset audit: generated + cleaned 4,929 rows with 0 boxed mismatches

  Stack: PyTorch, Unsloth, TRL, PEFT, Hugging Face Transformers, vLLM, Mamba, Triton, Kaggle GPU

  RESUME USE GUIDE (tell Claude which bullets to pick):
  - For LLM/fine-tuning roles: rank + score + staged continuation + ablation methodology
  - For research roles: error analysis insight (over-expansion vs. wrong reasoning) + custom solvers
  - For ML engineering roles: pipeline architecture + BF16 + LoRA targeting + grad_accum design
  - For NLP roles: chain-of-thought dataset, reasoning task taxonomy, format compliance challenge
  - ALWAYS include: "Ranked 17th / 3,000+ teams", "0.86/1.0", "$106K+", "30B-parameter", "LoRA SFT"
  - NEVER say the competition is finished — it is still in progress (deadline June 2026)

PROJECT 1: AI-Generated Image Detection — EURECOM ImSecu Course + NTIRE 2026 @ CVPR
  Achievement: Ranked 1st in EURECOM class (private Kaggle leaderboard, 0.791 AUC).
               Also submitted to NTIRE 2026 @ CVPR CodaBench international benchmark.
  Technical:
  - Backbone: CLIP ViT-L/14 (428M params, pretrained on 400M image-text pairs)
  - Custom MLP head: 768→512→256→1, GELU activations, BatchNorm, Dropout
  - Fine-tuning: unfroze last 4/6/8 transformer blocks, dual LR (2e-6 CLIP, 5e-4 head)
  - Key finding: 1-epoch training (0.793 AUC) outperforms 4-epoch (0.767 AUC) — OOD generalization
  - Ensemble: 3 models weighted by validation AUC; 10-view TTA (flips, crops, blur, resizes)
  - Training: 250K images, 25+ generator types, T4 GPU, FP16 mixed precision, AdamW
  - Stack: PyTorch, OpenCLIP, BCEWithLogitsLoss, Kaggle notebooks

PROJECT 2: Anomalous Sound Detection — Industrial Equipment (EURECOM, 2025)
  - Unsupervised fault detection for slide-rail machinery (no labeled anomalies)
  - Transformer autoencoder on Mel spectrograms with SpecAugment
  - Result: AUC 0.80 for predictive maintenance

PROJECT 3: Aerial Cactus Detection (EURECOM, 2025)
  - 17,500+ drone images, endangered species classification
  - Benchmarked CNN, DenseNet121, hybrid CNN-Transformer
  - Result: 99.8% accuracy, F1 = 0.999, ROC AUC = 1.0

PROJECT 4: Twitter Sentiment Analysis (EURECOM, 2025)
  - End-to-end NLP pipeline: tokenization → TF-IDF → word2vec → transformer fine-tuning
  - Hyperparameter optimization for sentiment classification

TECHNICAL SKILLS (only use these — do not invent)
Programming: Python (expert), SQL, Bash
ML/DL: PyTorch, TensorFlow, Scikit-learn, OpenCLIP, Hugging Face Transformers, Unsloth, TRL, PEFT
LLM Fine-tuning: LoRA, QLoRA, SFTTrainer, completion-only loss, Mamba-Transformer, MoE, vLLM, chain-of-thought
Computer Vision: CLIP, ViT, CNN, DenseNet, image classification
NLP: text preprocessing, embeddings, sentiment analysis, transformer models
AI/LLMs: Claude API, LLM orchestration, tool-calling, RAG, prompt engineering, LangChain, agentic pipelines, MCP server
Data Science: Pandas, NumPy, Matplotlib, Seaborn, feature engineering, EDA, clustering, ensemble methods
MLOps: Git, Linux, FP16/BF16 mixed precision, ablation studies, TTA, Kaggle GPU, CUDA debugging, Triton
Cloud/Infra: Supabase, REST APIs, Docker (basic), PostgreSQL
Visualization: Power BI, Matplotlib, Seaborn
Languages: English (C1), French (C1), Arabic (native)
"""

_RESUME_SCHEMA = """{
  "title": "Role-specific descriptor | JD-keyword1 · JD-keyword2 · JD-keyword3",
  "summary": "3-4 sentence paragraph. NO first person. NO 'passionate/motivated/dynamic'. Action verbs only.",
  "competencies": ["verbatim-jd-keyword1", "verbatim-jd-keyword2", "verbatim-jd-keyword3",
                   "verbatim-jd-keyword4", "verbatim-jd-keyword5", "verbatim-jd-keyword6",
                   "verbatim-jd-keyword7", "verbatim-jd-keyword8"],
  "experience": [
    {
      "company": "Company Name",
      "role": "Job Title",
      "period": "Mon YYYY – Mon YYYY",
      "location": "City, Country",
      "bullets": [
        "Action verb + what + <strong>measurable result or JD keyword</strong>",
        "Action verb + what + metric",
        "Action verb + what + metric",
        "Action verb + what + metric"
      ]
    }
  ],
  "projects": [
    {
      "name": "Project Name",
      "badge": "Context · Year · Key Achievement",
      "bullets": [
        "Action verb + what + <strong>key result with number</strong>",
        "Technical detail using JD vocabulary"
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
      "desc": "Relevant coursework: course1, course2, course3"
    },
    {
      "degree": "CPGE — Mathematics & Physics",
      "school": "Ibn Timiya",
      "period": "2021 – 2023",
      "location": "Marrakech, Morocco",
      "desc": ""
    }
  ],
  "skills": [
    {"category": "Most-relevant-to-JD Category", "items": "jd-keyword1 · jd-keyword2 · skill3"},
    {"category": "Second Category", "items": "skill1 · skill2 · skill3"},
    {"category": "Third Category", "items": "skill1 · skill2"}
  ],
  "ats_keywords_matched": ["keyword1", "keyword2"],
  "ats_keywords_missing": ["skill-not-in-profile"],
  "country_flag": "OK or WARNING: US role — verify visa eligibility",
  "tailoring_notes": "2-3 sentences on key changes made for this role"
}"""


# ── Resume variant classification ────────────────────────────────────────────

_VARIANT_GUIDANCE = {
    "research": (
        "\nVARIANT EMPHASIS — Research-Heavy:\n"
        "- Lead summary with Nemotron: rank 17/3000+, 0.86 score, staged continuation training insight\n"
        "- Project order: Nemotron first (use bullets A + D: achievement + error analysis insight), then NTIRE CLIP\n"
        "- Skills: lead with 'LLM Fine-tuning & Reasoning' category\n"
        "- Emphasize: task-level error analysis, ablation methodology, custom solver pipelines,\n"
        "  discovery that reasoning over-expansion (not wrong reasoning) is the key failure mode,\n"
        "  staged adapter continuation training as a fine-tuning technique, CVPR submission\n"
    ),
    "engineering": (
        "\nVARIANT EMPHASIS — ML Engineering:\n"
        "- Lead summary with Nemotron pipeline architecture + Orange Maroc production impact\n"
        "- Project order: Nemotron first (use bullets A + C: achievement + ablation/engineering), then CLIP\n"
        "- Skills: lead with 'ML Frameworks & Engineering' category\n"
        "- Emphasize: LoRA target module selection (attention + MLP + lm_head), BF16 training,\n"
        "  completion-only loss design, 8192-token context, grad_accum, end-to-end pipeline ownership\n"
    ),
    "analysis": (
        "\nVARIANT EMPHASIS — Data Analysis / Business-Facing:\n"
        "- Lead summary with Orange Maroc (100K records, KPIs, Power BI, ~60% time reduction)\n"
        "- Projects: cactus detection (accuracy/F1 metrics), Twitter sentiment (business NLP)\n"
        "- Mention Nemotron briefly in summary as a proof of technical depth, but don't lead with it\n"
        "- Skills: lead with 'Data Science & Analytics' category — SQL, Power BI, Pandas front\n"
        "- Emphasize: actionable insights, commercial service tiers, data storytelling\n"
    ),
}


def _classify_resume_variant(job: dict) -> str:
    """
    Determine the best resume variant for this job.
    Returns 'research' | 'engineering' | 'analysis'.
    """
    title_lower = (job.get("title") or "").lower()
    desc_lower  = (job.get("description") or "")[:600].lower()
    text        = title_lower + " " + desc_lower

    research_kws    = ["research", "llm", "fine-tun", "rlhf", "pretrain", "nlp", "language model",
                       "transformer", "scientist", "foundation model", "diffusion", "generative",
                       "multimodal", "paper", "lab", "phd", "scholar"]
    engineering_kws = ["engineer", "deploy", "mlops", "pipeline", "production", "infra",
                       "backend", "system", "scalab", "latency", "docker", "serving",
                       "real-time", "training infra", "platform", "devops"]
    analysis_kws    = ["analyst", "business", "insight", "sql", "bi", "tableau", "power bi",
                       "stakeholder", "report", "dashboard", "kpi", "visualization",
                       "a/b test", "growth", "product analytics", "excel"]

    r_score = sum(1 for kw in research_kws    if kw in text)
    e_score = sum(1 for kw in engineering_kws if kw in text)
    a_score = sum(1 for kw in analysis_kws    if kw in text)

    max_score = max(r_score, e_score, a_score)
    if max_score == 0 or r_score == max_score:
        return "research"   # default for generic ML/AI roles
    if e_score == max_score:
        return "engineering"
    return "analysis"


# ── Company research ──────────────────────────────────────────────────────────

def _research_company(company: str, job_title: str) -> str:
    """
    Light company research via DuckDuckGo Instant Answer API.
    Returns 1-3 sentences of context, or empty string on failure.
    Wrapped in try/except — research failure must never block doc generation.
    """
    import urllib.parse
    try:
        import requests as _req
        query = f"{company} company technology"
        url = (
            "https://api.duckduckgo.com/?q="
            + urllib.parse.quote(query)
            + "&format=json&no_redirect=1&no_html=1&skip_disambig=1"
        )
        r = _req.get(url, timeout=5, headers={"User-Agent": "career-ops/1.0"})
        r.raise_for_status()
        data = r.json()

        parts = []
        abstract = (data.get("AbstractText") or "").strip()
        if abstract and len(abstract) > 40:
            parts.append(abstract[:400])

        for topic in (data.get("RelatedTopics") or [])[:2]:
            text = (topic.get("Text") or "").strip()
            if text and len(text) > 30:
                parts.append(text[:200])

        result = " ".join(parts)[:600]
        if result:
            logger.debug("Company research for %s: %d chars", company, len(result))
        return result
    except Exception as exc:
        logger.debug("Company research skipped for %s: %s", company, exc)
        return ""


def _call_resume_content(job: dict) -> dict:
    title   = job.get("title", "")
    company = job.get("company", "")
    region  = job.get("region", "")
    desc    = (job.get("description") or "")[:3500]

    variant        = _classify_resume_variant(job)
    variant_block  = _VARIANT_GUIDANCE.get(variant, "")
    job["_resume_variant"] = variant   # store for logging
    logger.info("  Resume variant: %s", variant)

    prompt = f"""You are an expert ATS resume writer. Produce a perfectly ATS-optimized resume
tailored to the specific role below. Follow every rule exactly.

━━━ CANDIDATE PROFILE (source of truth — never invent or change these facts) ━━━
{_CANDIDATE_FULL}

━━━ TARGET ROLE ━━━
Company: {company}
Title: {title}
Region: {region}
Job description:
{desc if desc else "(no description provided — infer from role title and company context)"}
{variant_block}

━━━ STEP 1 — KEYWORD EXTRACTION (do this internally first) ━━━
Extract from the JD:
1. Hard skills mentioned (Python, SQL, PyTorch, etc.)
2. Domain words (computer vision, NLP, agentic, MLOps, etc.)
3. Action verbs in responsibilities (evaluate, deploy, prototype, etc.)
4. Qualifications listed (Master's, internship, 6 months, etc.)
Every keyword the candidate genuinely has MUST appear VERBATIM in the resume.
Synonyms are allowed only when they mean exactly the same thing (e.g. "segmentation" = "clustering").

━━━ STEP 2 — SECTION RULES ━━━

TITLE: Mirror the JD language exactly. Not generic.
Example for an NLP role: "NLP Research Engineer | Transformer Fine-Tuning · Text Classification · LLM Deployment"

SUMMARY (3-4 sentences, STRICT):
- Sentence 1: strongest proof point that maps directly to the #1 JD requirement — include a number or ranking
- Sentence 2: second achievement using JD vocabulary with a concrete metric (%, AUC, rank, time saved)
- Sentence 3: bridge sentence — 2-3 skills verbatim from JD requirements + what the candidate brings to THIS team
- Sentence 4: forward-looking — what the candidate aims to contribute or explore in this specific role/company domain
BANNED words: passionate, enthusiastic, dynamic, motivated, hardworking, leverage, spearheaded, pioneered
BANNED content: work authorization, visa, regulations, citizenship, legal status — NEVER mention these
NO first person (no "I" or "My")

COMPETENCIES: 8 tags taken VERBATIM from the JD requirements. These are ATS scan targets.

SKILLS SECTION:
- Lead with the category most relevant to this job (if CV role → Computer Vision first; if NLP → NLP/Text first)
- Every JD keyword the candidate has must appear here
- Do NOT list skills not in the candidate profile

EXPERIENCE BULLETS:
- Format: Past-tense verb + what + measurable result
- EXACTLY 4 bullets — never fewer, never more
- At least 3 of 4 bullets must contain a number (%, rows, AUC, team size)
- Reorder bullets: most JD-relevant first
- Use exact JD terminology in at least 2 bullets
- Bold (<strong>) the single most impressive metric or JD keyword per bullet
- Write full, dense sentences — do not truncate. Each bullet must be 15-25 words.

PROJECTS — select 2 projects that best match THIS specific role (3 only if JD spans 3 distinct domains):
- Choose the 2 projects whose technical content most directly matches the JD keywords and responsibilities
- Default to 2 projects — a focused, well-chosen 2 beats a diluted 3
- Add a 3rd project ONLY if the JD explicitly requires 3 very different technical areas (e.g. LLM + CV + audio)
- PROJECT 0 (Nemotron) should be included for any role involving: LLM, fine-tuning, LoRA, PEFT, research, NLP, reasoning, Kaggle, large models, training, ablation studies — which covers most ML roles
- PROJECT 1 (NTIRE/CLIP) should be included for: CV, vision, image, CLIP, ViT, classification
- PROJECT 2 (audio) should be included for: audio, speech, signal processing, anomaly detection
- PROJECT 3 (cactus/DenseNet) should be included for: CV without strong LLM component
- PROJECT 4 (Twitter) should be included for: pure NLP without LLM fine-tuning component
- Reorder: the single most JD-relevant project first
- EXACTLY 2 bullets per project — full sentences, 12-20 words each
- For Project 0 (Nemotron): EXACTLY 2 bullets chosen based on the JD:
  * BULLET OPTION A — Achievement + Pipeline: "Ranked <strong>17th / 3,000+ teams</strong> (score 0.86/1.0, $106K+ prize) in the NVIDIA Nemotron Reasoning Challenge by fine-tuning a <strong>30B-parameter Mamba-Transformer</strong> with LoRA SFT (r=32, BF16, completion-only loss, 8,192-token context)."
  * BULLET OPTION B — Staged continuation: "Improved leaderboard score from 0.85 to <strong>0.86</strong> through staged adapter continuation training; diagnosed that hard-task failures (cipher, bit manipulation) stemmed from <strong>reasoning over-expansion</strong>, not wrong reasoning."
  * BULLET OPTION C — Ablation/engineering: "Ran <strong>controlled ablation experiments</strong> across LR schedules, packing strategies, and synthetic augmentation — discovering that reasoning-style consistency outweighs data volume in LLM SFT; built custom cipher parsers and 8-bit operation solvers for verified CoT data."
  * BULLET OPTION D — Research/analysis: "Performed <strong>task-level error analysis</strong> across 5 reasoning categories (unit conversion, cipher, symbolic, gravity, bit manipulation); built diagnostic evaluation pipeline revealing that over-expanded reasoning — not incorrect reasoning — was the primary failure mode."
  * For LLM/fine-tuning JDs: use A + B
  * For research JDs: use A + D
  * For ML engineering JDs: use A + C
  * For NLP/reasoning JDs: use B + D
  * ALWAYS include "Ranked 17th / 3,000+ teams", "0.86/1.0", "$106K+", "30B-parameter", "LoRA"
  * NEVER say the competition is finished — it is still in progress (deadline June 2026)
- For Project 1 (NTIRE): always state "Ranked 1st in EURECOM class leaderboard" AND "submitted to NTIRE 2026 @ CVPR CodaBench" — never conflate the two
- Always include the "tech" field with 3-5 tools

EDUCATION:
- Include BOTH education entries (EURECOM + CPGE)
- Include relevant coursework for EURECOM if courses match JD requirements

SKILLS: EXACTLY 3 skill rows, each with 5-7 items separated by " | " (pipe). No dots, no bullets.

━━━ STEP 3 — ONE-PAGE DENSITY RULES (CRITICAL) ━━━
The rendered resume must fill exactly ONE full A4/Letter page — no more, no less.
To achieve this:
- Summary: exactly 4 sentences, each 20-30 words. Do NOT shorten.
- Competencies: exactly 8 items, each 2-4 words
- Experience: exactly 4 bullets as specified above
- 2 projects with exactly 2 bullets each (or 3 if justified by JD breadth)
- Both education entries
- 3 skill rows with 5-7 items each
Do not produce thin, short content — fill every section completely.

━━━ STEP 4 — HARD RULES ━━━
- NEVER invent metrics, tools, projects, or experience not in the profile above
- No em-dashes (use -), no smart quotes, no Unicode bullets (only plain hyphens in text)
- No "References available upon request"
- No "etc." in bullets — be specific or cut
- Do not repeat the same action verb twice in the same section
- Do not include photo, nationality, date of birth, or marital status

━━━ OUTPUT ━━━
Return ONLY valid JSON matching this exact schema. No markdown fences, no explanation.

{_RESUME_SCHEMA}
"""

    msg = _get_client().messages.create(
        model=CLAUDE_MODEL,
        max_tokens=3500,
        messages=[{"role": "user", "content": prompt}],
    )
    record_usage(msg.usage.input_tokens, msg.usage.output_tokens, label="resume")
    raw = _parse_json(msg.content[0].text)
    # Log ATS metadata without breaking the pipeline
    if raw.get("ats_keywords_missing"):
        logger.info("  ATS missing keywords: %s", raw["ats_keywords_missing"])
    if raw.get("country_flag", "").startswith("WARNING"):
        logger.warning("  %s", raw["country_flag"])
    if raw.get("tailoring_notes"):
        logger.info("  Tailoring: %s", raw["tailoring_notes"])
    return raw


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

    # Company research — inject into prompt for a more specific paragraph 3
    research = _research_company(company, title)
    research_block = (
        f"\n## Company research (verified facts to use in paragraph 3)\n{research}\n"
        if research else ""
    )

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
{research_block}

## Instructions

### Paragraph 1 — Hook (under 80 words)
- Open with a single, specific JD requirement pulled verbatim from the description
- Immediately map it to your single strongest proof point (e.g. "#1 in EURECOM class leaderboard + submitted to NTIRE 2026 @ CVPR",
  "100K-record ML pipeline")
- Bold 2-3 key terms with <strong>
- Do NOT start with "I am" or "I would like"

### Paragraph 2 — Proof (under 100 words)
- Two concrete achievements with exact numbers, directly mapped to two more JD requirements
- Use the exact technical terms from the JD
- Bold every metric and tool name with <strong>
- These must be different proof points from paragraph 1

### Paragraph 3 — Why {company} (under 60 words)
- Use the company research block above if provided — cite a specific fact (product, recent launch, tech stack, market position)
- If no research provided, infer from JD context and company name/sector
- Direct call to action — one sentence
- No clichés, no "excited opportunity", no "passionate about"

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
    # Competencies rendered as plain pipe-separated text (ATS-friendly, no colored tags)
    competencies = " | ".join(data.get("competencies", []))

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
        <span class="project-context">{p.get("badge","")}</span>
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

        # ── ATS score (free — uses Claude's own keyword metadata) ────────────
        matched = resume_data.get("ats_keywords_matched") or []
        missing = resume_data.get("ats_keywords_missing") or []
        total_kw = len(matched) + len(missing)
        if total_kw > 0:
            ats_score = int(len(matched) / total_kw * 100)
        else:
            ats_score = 85   # default when no keyword data

        job["ats_score"]   = ats_score
        job["ats_missing"] = missing

        if ats_score < 60 and missing:
            logger.warning(
                "  ⚠️ ATS score low (%d%%) for %s @ %s — missing: %s",
                ats_score, title, company, ", ".join(missing[:5]),
            )
        else:
            logger.info("  ATS score: %d%%  matched=%d  missing=%d", ats_score, len(matched), len(missing))

        html      = _fill_resume_html(resume_data, fmt)
        html_file = tmp_dir / f"cv-{cand_slug}-{co_slug}.html"
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
        _exc_str = str(exc)
        if "credit balance is too low" in _exc_str or "insufficient_quota" in _exc_str:
            raise RuntimeError(f"Anthropic credit balance exhausted: {_exc_str}") from exc
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
        _exc_str = str(exc)
        if "credit balance is too low" in _exc_str or "insufficient_quota" in _exc_str:
            raise RuntimeError(f"Anthropic credit balance exhausted: {_exc_str}") from exc
        logger.error("Cover letter failed for %s @ %s: %s", title, company, exc)

    return resume_path, cover_path
