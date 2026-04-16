# Internship Finder — Automated Career-Ops

> A fork of [career-ops](https://github.com/santifer/career-ops) extended with a fully automated internship search pipeline.

<p align="center">
  <img src="https://img.shields.io/badge/Claude_Sonnet_4.6-000?style=flat&logo=anthropic&logoColor=white" alt="Claude">
  <img src="https://img.shields.io/badge/Python-3776AB?style=flat&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Node.js-339933?style=flat&logo=node.js&logoColor=white" alt="Node.js">
  <img src="https://img.shields.io/badge/Playwright-2EAD33?style=flat&logo=playwright&logoColor=white" alt="Playwright">
  <img src="https://img.shields.io/badge/Apify-1ABCFF?style=flat&logo=apify&logoColor=white" alt="Apify">
  <img src="https://img.shields.io/badge/GitHub_Actions-2088FF?style=flat&logo=github-actions&logoColor=white" alt="GitHub Actions">
  <img src="https://img.shields.io/badge/Telegram-26A5E4?style=flat&logo=telegram&logoColor=white" alt="Telegram">
  <img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="MIT">
</p>

---

## What This Fork Adds

The original career-ops is an interactive Claude Code skill — you paste a JD, Claude evaluates it, generates a tailored PDF. **This fork automates the entire pipeline end-to-end:**

| | career-ops (upstream) | This fork |
|---|---|---|
| Job discovery | Manual (you paste JDs) | **Automatic** — scrapes 4 job boards 2×/day |
| Relevance filter | Manual review | **Claude scores each job 1–10**, drops below 7 |
| Document generation | Interactive session | **Fully automated** per compatible offer |
| Notifications | None | **Telegram reports** with scores + apply links |
| Tracking | TSV / markdown | **Excel tracker** updated after every run |
| Scheduling | On-demand | **GitHub Actions** at 8AM & 8PM Paris time |

---

## How It Works

```
GitHub Actions (8AM / 8PM Paris)
          │
          ▼
┌─────────────────────┐
│   Scraper (Apify)   │  Indeed · LinkedIn · Glassdoor · Wellfound
│   4 job boards      │  Europe 60% · Asia 30% · USA/CA 10%
│   ~20 regions       │  Keywords: data science · ML · AI · data analyst
└──────────┬──────────┘
           │  raw jobs (deduplicated)
           ▼
┌─────────────────────┐
│  Relevance Filter   │  Title keyword pre-screen (free, instant)
│  Claude Sonnet 4.6  │  Claude scores ambiguous titles 1–10
│  min score: 7/10    │  Rejected jobs saved to seen_ids (never re-scored)
└──────────┬──────────┘
           │  compatible jobs only
           ▼
┌─────────────────────┐
│  Doc Generator      │  Step 1: Claude generates structured JSON content
│  career-ops HTML    │          (summary · bullets · competencies · cover)
│  → PDF pipeline     │  Step 2: Python fills HTML template
│                     │  Step 3: node generate-pdf.mjs → PDF via Playwright
└──────────┬──────────┘
           │  resume.pdf + cover_letter.pdf per job
           ▼
┌─────────────────────┐
│  Tracker + Notify   │  Excel tracker updated (openpyxl)
│                     │  Telegram: scores · apply links · cost summary
└─────────────────────┘
```

---

## PDF Design

Documents use the original career-ops design system:

- **Fonts:** Space Grotesk (headings) + DM Sans (body) — self-hosted woff2
- **Header:** Dark navy (`#1a1a2e`) with white name, subtitle, institution top-right
- **Accents:** Teal `hsl(187,74%,32%)` for section headers · Purple `hsl(270,70%,45%)` for company names
- **ATS:** Single-column layout, UTF-8, selectable text, Unicode normalization via `generate-pdf.mjs`
- **Cover letter:** Matching design — metadata line, horizontal dividers, recipient block, footer

---

## Setup

### Prerequisites

```bash
# Python 3.11+
pip install -r requirements.txt

# Node.js 20+
npm install
npx playwright install chromium --with-deps
```

### Environment variables

Create a `.env` file in the project root (never committed):

```env
ANTHROPIC_API_KEY=sk-ant-...
APIFY_API_TOKEN=apify_api_...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
OUTPUT_DIR=/path/to/career-ops   # where resumes/ and cover_letters/ are saved
```

### Candidate profile

Edit these three files with your own information:

| File | Purpose |
|------|---------|
| `cv.md` | Your full CV in markdown — single source of truth for document generation |
| `config/profile.yml` | Identity, contact info, target roles, comp targets |
| `modes/_profile.md` | Archetype framing, adaptive narrative per role type |

### Run locally

```bash
python3 main.py
```

### Automated runs (GitHub Actions)

1. Push this repo to GitHub
2. Add the four secrets under **Settings → Secrets → Actions**:
   - `ANTHROPIC_API_KEY`
   - `APIFY_API_TOKEN`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
3. The workflow runs automatically at **06:00 UTC** (8AM Paris) and **18:00 UTC** (8PM Paris)
4. Trigger manually anytime: **Actions → Internship Finder → Run workflow**

---

## Telegram Reports

Every run sends three messages:

**1. Same-hour alert** (if a job was posted within the current hour)
```
🔥 SAME-HOUR OFFER — Apply now!

🔷 Machine Learning Intern
🏢 DeepMind
📍 London, UK  |  🌍 Europe
⭐ Match: 9/10 — strong CLIP/ViT fine-tuning alignment
🔗 View & Apply
```

**2. Full compatible offers report**
```
💼 12 compatible internships found
🇪🇺 EU: 7  🌏 Asia: 3  🇺🇸 US/CA: 2
🔵 Indeed 5 · 🔷 LinkedIn 4 · 🟢 Glassdoor 2 · 🟠 Wellfound 1
🔍 Scraped: 94  →  ✅ Compatible: 12  ✗ Filtered: 82

1. ⭐9/10 Machine Learning Intern @ DeepMind
   📍 London  |  Apply →
   💡 strong CLIP/ViT fine-tuning alignment
...
```

**3. Run summary**
```
✅ Run complete
New compatible offers: 12
Total in tracker: 47
Scraped: 94  →  Compatible: 12  |  Filtered: 82
💰 Claude today: $0.48 / $3.00 (16%)
```

---

## Project Structure

```
career-ops/
├── main.py                      # Pipeline orchestrator
├── scraper.py                   # Apify actors: Indeed, LinkedIn, Glassdoor, Wellfound
├── job_filter.py                # Relevance scoring (title pre-screen + Claude)
├── doc_generator.py             # Claude JSON → HTML template → PDF
├── notifier.py                  # Telegram notifications
├── tracker_manager.py           # Excel tracker (openpyxl)
├── credit_monitor.py            # Anthropic spend tracking + budget alerts
├── config.py                    # All configuration (regions, keywords, thresholds)
├── requirements.txt             # Python dependencies
│
├── cv.md                        # ★ Your CV — edit this
├── config/
│   └── profile.yml              # ★ Your identity & targets — edit this
├── modes/
│   ├── _profile.md              # ★ Your archetype framing — edit this
│   ├── _shared.md               # career-ops system context (upstream)
│   └── pdf.md                   # PDF generation rules (upstream)
│
├── templates/
│   ├── cv-template.html         # Resume HTML template (dark navy header)
│   └── cover-template.html      # Cover letter HTML template (matching design)
├── generate-pdf.mjs             # Node.js Playwright HTML→PDF renderer
├── fonts/                       # Space Grotesk + DM Sans woff2
│
├── .github/
│   └── workflows/
│       └── internship-finder.yml  # 2×/day GitHub Actions schedule
│
├── data/                        # Runtime data (gitignored)
│   ├── tracker.xlsx             # Application tracker
│   ├── seen_job_ids.txt         # Deduplication memory
│   ├── token_usage.json         # Claude spend tracking
│   └── run.log                  # Run logs
├── resumes/                     # Generated resume PDFs (gitignored)
├── cover_letters/               # Generated cover letter PDFs (gitignored)
└── tmp/                         # Intermediate HTML files (gitignored)
```

---

## Configuration

Key settings in `config.py`:

```python
# Regions (weighted distribution)
# Europe 60% · Asia 30% · USA/Canada 10%
REGIONS = { ... }

# Search keywords
SEARCH_KEYWORDS = ["data science intern", "machine learning intern", "AI intern", "data analyst intern"]

# Relevance threshold — jobs below this score are filtered out
MIN_RELEVANCE_SCORE = 7   # out of 10

# Results per search (per country/keyword combo)
RESULTS_PER_SEARCH = 10

# Company blacklist
COMPANY_BLACKLIST = ["tiktok", "bytedance"]

# Daily Claude budget alert
ANTHROPIC_DAILY_BUDGET_USD = 3.00

# Model
CLAUDE_MODEL = "claude-sonnet-4-6"
```

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Orchestration | Python 3.11 |
| Job scraping | [Apify](https://apify.com) — Indeed, LinkedIn, Glassdoor, Wellfound actors |
| Relevance scoring | Claude Sonnet 4.6 API |
| Document generation | Claude Sonnet 4.6 API → HTML → Playwright PDF |
| PDF rendering | Node.js + Playwright/Chromium (`generate-pdf.mjs`) |
| Scheduling | GitHub Actions (cron) |
| Notifications | Telegram Bot API |
| Tracking | Excel (openpyxl) |
| Fonts | Space Grotesk + DM Sans (self-hosted woff2) |

---

## Credits

This project is built on top of **[career-ops](https://github.com/santifer/career-ops)** by [Santiago Fernández](https://santifer.io) — the original AI-powered job search system for Claude Code. The PDF design system, HTML templates, `generate-pdf.mjs`, fonts, and `modes/_shared.md` / `modes/pdf.md` are from the upstream repo.

What this fork adds: automated scraping, relevance filtering, scheduled execution, Telegram reporting, and Excel tracking — turning an interactive tool into a fully autonomous pipeline.

---

## License

MIT — see [LICENSE](LICENSE)
