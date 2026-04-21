"""Central configuration for the Internship Finder system."""

import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env", override=True)

# ── API Keys ──────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY", "")
APIFY_API_TOKEN    = os.getenv("APIFY_API_TOKEN", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Folders ───────────────────────────────────────────────────────────────────
OUTPUT_DIR        = Path(os.getenv("OUTPUT_DIR", BASE_DIR))
RESUMES_DIR       = OUTPUT_DIR / "resumes"
COVERS_DIR        = OUTPUT_DIR / "cover_letters"
DATA_DIR          = OUTPUT_DIR / "data"
TRACKER_PATH           = DATA_DIR / "tracker.xlsx"
SEEN_IDS_PATH          = DATA_DIR / "seen_job_ids.txt"
SEEN_FINGERPRINTS_PATH = DATA_DIR / "seen_fingerprints.txt"

for d in (RESUMES_DIR, COVERS_DIR, DATA_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ── Candidate profile (used by Claude for doc generation) ────────────────────
CANDIDATE = {
    "name":       "AYMANE AIT DADS",
    "title":      "Data Science Engineer | LLM Research · Transformer Fine-Tuning · ML Systems",
    "email":      "Aymane.Ait-dads@eurecom.fr",
    "phone":      "+33 7 60 92 50 93",
    "linkedin":   "linkedin.com/in/aymane-ait-dads",
    "location":   "Sophia Antipolis, France",
    "school":     "EURECOM",
    "degree":     "Engineering Degree in Data Science",
    "degree_dates": "Sep 2023 – Present",
    "coursework": "Machine Learning · Deep Learning · Computer Vision · NLP · Cloud Computing · Image Security · Statistics",
    "prep_school": "Preparatory Classes (CPGE) in Mathematics & Physics — Ibn Timiya, Marrakech",
    "prep_dates": "Sep 2021 – Jun 2023",
    "experience": (
        "Data Science Intern — Orange Maroc, Casablanca (Jul–Aug 2024): "
        "Engineered an end-to-end ML pipeline on 100K+ telemetry records (Random Forest + KMeans), "
        "identifying the top 3 network-quality drivers and reducing manual analysis time by ~60%. "
        "Delivered KMeans segmentation mapping 5 user performance profiles to commercial service tiers. "
        "Applied feature engineering, cross-validated model evaluation, and predictive maintenance modeling; "
        "independently owned the full pipeline from raw data ingestion to executive-ready Power BI dashboards."
    ),
    "projects": [
        {
            "name": "Robust AI-Generated Image Detection",
            "context": "EURECOM • 2026",
            "bullets": [
                "Ranked 1st in EURECOM class leaderboard (private Kaggle, 0.791 AUC) and submitted to NTIRE 2026 @ CVPR CodaBench — "
                "fine-tuned CLIP ViT-L/14 (428M params) on 250K samples spanning 25+ generator types; FP16/GradScaler on T4 GPU, 10-view TTA for OOD-robust inference.",
                "Designed ablation experiments across epoch count, transformer block unfreezing depth (4/6/8 layers), LR, and augmentation; "
                "proved 1-epoch fine-tuning preserves generalization better than extended re-training. Stack: PyTorch · OpenCLIP.",
            ],
        },
        {
            "name": "Anomalous Sound Detection in Industrial Equipment",
            "context": "EURECOM • 2025",
            "bullets": [
                "Built transformer autoencoder for unsupervised fault detection on industrial audio (AUC 0.80) "
                "using Mel spectrograms and SpecAugment — no labeled anomalies required.",
                "Benchmarked Mel spectrogram vs. MFCC feature representations; evaluated on a DCASE-style protocol.",
            ],
        },
        {
            "name": "Aerial Cactus Detection & Twitter Sentiment Analysis",
            "context": "EURECOM • 2025",
            "bullets": [
                "Achieved 99.8% accuracy (F1=0.999, AUC=1.0) on 17,500+ aerial images by benchmarking CNN, DenseNet121, and CNN-Transformer.",
                "Built complete NLP tweet-sentiment pipeline: tokenization → TF-IDF baseline → word2vec embeddings → transformer fine-tuning.",
            ],
        },
    ],
    "skills": {
        "LLM & NLP":          "Transformers (CLIP ViT-L/14) | Autoregressive Models | Fine-tuning | TTA | Ensemble Methods | Hugging Face",
        "ML Frameworks":      "PyTorch | TensorFlow | OpenCLIP | FP16 Mixed-Precision | Scikit-learn | Pandas | NumPy",
        "Programming & DevOps": "Python (advanced) | SQL | Git | Linux | Matplotlib | Power BI",
    },
    "languages": "English (fluent) • French (fluent) • Arabic (native) • Spanish (intermediate)",
}

# ── Scraping targets ──────────────────────────────────────────────────────────
# Europe 45% · Asia 25% · USA/Canada 15% · South America 10% · Middle East 5%
REGIONS = {
    "Europe": {
        "weight": 0.45,
        "searches": [
            {"country": "uk",  "location": "London"},
            {"country": "de",  "location": "Berlin"},
            {"country": "de",  "location": "Munich"},
            {"country": "nl",  "location": "Amsterdam"},
            {"country": "se",  "location": "Stockholm"},
            {"country": "ch",  "location": "Zurich"},
            {"country": "es",  "location": "Barcelona"},
            {"country": "es",  "location": "Madrid"},
            {"country": "ie",  "location": "Dublin"},
            {"country": "be",  "location": "Brussels"},
            {"country": "dk",  "location": "Copenhagen"},
            {"country": "no",  "location": "Oslo"},
            {"country": "fi",  "location": "Helsinki"},
            {"country": "it",  "location": "Milan"},
            {"country": "pt",  "location": "Lisbon"},
            {"country": "pl",  "location": "Warsaw"},
            {"country": "cz",  "location": "Prague"},
            {"country": "at",  "location": "Vienna"},
            {"country": "ro",  "location": "Bucharest"},
            {"country": "hu",  "location": "Budapest"},
        ],
    },
    "Asia": {
        "weight": 0.25,
        "searches": [
            {"country": "sg", "location": "Singapore"},
            {"country": "jp", "location": "Tokyo"},
            {"country": "kr", "location": "Seoul"},
            {"country": "hk", "location": "Hong Kong"},
            {"country": "in", "location": "Bangalore"},
            {"country": "in", "location": "Mumbai"},
            {"country": "my", "location": "Kuala Lumpur"},
            {"country": "tw", "location": "Taipei"},
            {"country": "th", "location": "Bangkok"},
            {"country": "id", "location": "Jakarta"},
        ],
    },
    "USA_Canada": {
        "weight": 0.15,
        "searches": [
            {"country": "us", "location": "New York"},
            {"country": "us", "location": "San Francisco"},
            {"country": "us", "location": "Boston"},
            {"country": "us", "location": "Seattle"},
            {"country": "ca", "location": "Toronto"},
            {"country": "ca", "location": "Montreal"},
        ],
    },
    "South_America": {
        "weight": 0.10,
        "searches": [
            {"country": "br", "location": "São Paulo"},
            {"country": "br", "location": "Rio de Janeiro"},
            {"country": "ar", "location": "Buenos Aires"},
            {"country": "co", "location": "Bogotá"},
            {"country": "cl", "location": "Santiago"},
            {"country": "mx", "location": "Mexico City"},
        ],
    },
    "Middle_East": {
        "weight": 0.05,
        "searches": [
            {"country": "ae", "location": "Dubai"},
            {"country": "ae", "location": "Abu Dhabi"},
        ],
    },
}

SEARCH_KEYWORDS    = ["data science intern", "machine learning intern", "AI intern", "data analyst intern"]

# ── Company blacklist (case-insensitive) ──────────────────────────────────────
# Add any company name (or substring) you never want to see.
# Format: lowercase strings. Checked against job["company"].lower().
COMPANY_BLACKLIST: list = [
    "tiktok",
    "bytedance",    # TikTok parent company
]

# ── Relevance filter ──────────────────────────────────────────────────────────
# Jobs scored below this (out of 10) by Claude are discarded before doc generation.
# 7 = "good fit or better" — adjust up (stricter) or down (more permissive).
MIN_RELEVANCE_SCORE: int = 7
RESULTS_PER_SEARCH = 10    # per country/keyword combo (Indeed + Glassdoor)
DATE_POSTED        = "3"   # last 3 days (Indeed) — avoids missing jobs from same 24h window
GLASSDOOR_DAYS_OLD = 3     # last 3 days (Glassdoor)
LINKEDIN_HOURS     = 259200 # last 3 days in seconds (LinkedIn f_TPR param)
LINKEDIN_COUNT     = 30     # results per URL — higher than Indeed/Glassdoor to compensate for filtering
WELLFOUND_MAX      = 30    # results per keyword (Wellfound — free actor, no date filter)
MAX_JOB_AGE_DAYS   = 3     # drop jobs with posted_at older than this (post-scrape age filter)

# ── Apify actor IDs ───────────────────────────────────────────────────────────
ACTOR_INDEED       = "valig/indeed-jobs-scraper"
ACTOR_LINKEDIN     = "curious_coder/linkedin-jobs-scraper"
ACTOR_GLASSDOOR    = "valig/glassdoor-jobs-scraper"
ACTOR_WELLFOUND    = "sovereigntaylor/wellfound-scraper"
ACTOR_GOOGLE_JOBS  = "apify/google-jobs-scraper"

# ── LinkedIn URL builder ──────────────────────────────────────────────────────
# Regions for LinkedIn (city name used in URL, LinkedIn split country code)
LINKEDIN_REGIONS = {
    "Europe": [
        ("London",      "GB"),
        ("Berlin",      "DE"),
        ("Munich",      "DE"),
        ("Amsterdam",   "NL"),
        ("Stockholm",   "SE"),
        ("Zurich",      "CH"),
        ("Barcelona",   "ES"),
        ("Madrid",      "ES"),
        ("Dublin",      "IE"),
        ("Brussels",    "BE"),
        ("Copenhagen",  "DK"),
        ("Helsinki",    "FI"),
        ("Milan",       "IT"),
        ("Lisbon",      "PT"),
        ("Warsaw",      "PL"),
        ("Prague",      "CZ"),
        ("Vienna",      "AT"),
    ],
    "Asia": [
        ("Singapore",    "SG"),
        ("Tokyo",        "JP"),
        ("Seoul",        "KR"),
        ("Hong Kong",    "HK"),
        ("Bangalore",    "IN"),
        ("Mumbai",       "IN"),
        ("Kuala Lumpur", "MY"),
        ("Bangkok",      "TH"),
    ],
    "USA_Canada": [
        ("New York",      "US"),
        ("San Francisco", "US"),
        ("Boston",        "US"),
        ("Seattle",       "US"),
        ("Toronto",       "CA"),
        ("Montreal",      "CA"),
    ],
    "South_America": [
        ("São Paulo",     "BR"),
        ("Buenos Aires",  "AR"),
        ("Bogotá",        "CO"),
        ("Santiago",      "CL"),
        ("Mexico City",   "MX"),
    ],
    "Middle_East": [
        ("Dubai",         "AE"),
    ],
}

def linkedin_url(keyword: str, location: str) -> str:
    """
    Build a public LinkedIn jobs search URL sorted by date.

    f_JT=I (Internship type) is intentionally REMOVED — the majority of
    internship postings on LinkedIn are NOT tagged with the Internship job type.
    Keeping that filter cuts 60-70%% of real internship results.
    The relevance filter (job_filter.py) handles non-internship rejection instead.
    """
    import urllib.parse
    params = {
        "keywords": keyword,
        "location": location,
        "f_TPR":    f"r{LINKEDIN_HOURS}",   # last 3 days
        "sortBy":   "DD",                   # date descending — most recent first
        "position": "1",
        "pageNum":  "0",
    }
    return "https://www.linkedin.com/jobs/search/?" + urllib.parse.urlencode(params)

# ── Anthropic credit monitoring ───────────────────────────────────────────────

# Set ANTHROPIC_TOTAL_CREDIT_USD to the amount you've actually loaded into
# your Anthropic account (check console.anthropic.com → Billing).
# The system tracks ALL-TIME cumulative spend against this and shows the real
# remaining balance in every Telegram report.
# Also add it as a GitHub Secret so it travels with the Actions runner.
ANTHROPIC_TOTAL_CREDIT_USD = float(os.getenv("ANTHROPIC_TOTAL_CREDIT_USD", "20.00"))

# Daily soft-cap: if estimated daily spend exceeds this, send a warning alert.
# This is a secondary guard on top of the account-level balance tracker.
ANTHROPIC_DAILY_BUDGET_USD = float(os.getenv("ANTHROPIC_DAILY_BUDGET_USD", "3.00"))

# ── Claude model ──────────────────────────────────────────────────────────────
CLAUDE_MODEL = "claude-sonnet-4-6"

# ── Sonnet 4.6 pricing (USD per million tokens) ───────────────────────────────
CLAUDE_INPUT_COST_PER_MTOK  = 3.00
CLAUDE_OUTPUT_COST_PER_MTOK = 15.00
