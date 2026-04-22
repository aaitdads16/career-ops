"""
dashboard_generator.py — Generate a world-class analytics dashboard.

Reads tracker.xlsx + scored_jobs.jsonl + token_usage.json.
Outputs docs/index.html → served by GitHub Pages.

Run manually:    python3 dashboard_generator.py
Auto-triggered: after every main.py run via GitHub Actions.
"""

import json
import logging
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import DATA_DIR
from tracker_manager import get_all_jobs

logger = logging.getLogger(__name__)

BASE_DIR    = Path(__file__).parent
DOCS_DIR    = BASE_DIR / "docs"
OUTPUT      = DOCS_DIR / "index.html"
SCORED_PATH    = DATA_DIR / "scored_jobs.jsonl"
TOKEN_PATH     = DATA_DIR / "token_usage.json"
ADVICE_PATH    = DATA_DIR / "skills_gap_advice.txt"
REJECTION_PATH = DATA_DIR / "rejection_analysis.txt"
STATUSES_PATH  = DATA_DIR / "statuses.json"


# ── Data loading ──────────────────────────────────────────────────────────────

def _load_scored_jobs(days: int = 30) -> list:
    if not SCORED_PATH.exists():
        return []
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    out = []
    try:
        with open(str(SCORED_PATH), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    ts = datetime.fromisoformat(r.get("ts", "2000-01-01T00:00:00+00:00"))
                    if ts >= cutoff:
                        out.append(r)
                except Exception:
                    pass
    except Exception as exc:
        logger.warning("Could not load scored_jobs: %s", exc)
    return out


def _load_token_usage() -> dict:
    if not TOKEN_PATH.exists():
        return {}
    try:
        return json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_advice() -> str:
    if not ADVICE_PATH.exists():
        return ""
    try:
        return ADVICE_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _load_rejection_analysis() -> str:
    if not REJECTION_PATH.exists():
        return ""
    try:
        return REJECTION_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _load_statuses() -> dict:
    """Load manual status overrides from data/statuses.json."""
    if not STATUSES_PATH.exists():
        return {}
    try:
        return json.loads(STATUSES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ── Analytics computation ─────────────────────────────────────────────────────

def _compute_stats():
    jobs       = get_all_jobs()
    scored     = _load_scored_jobs(days=30)
    token_data = _load_token_usage()
    advice            = _load_advice()
    rejection_analysis = _load_rejection_analysis()
    now        = datetime.now(tz=timezone.utc)

    # Status breakdown
    status_counts = Counter((j.get("Status") or "Waiting to apply") for j in jobs)
    applied   = status_counts.get("Applied", 0)
    waiting   = status_counts.get("Waiting to apply", 0)
    rejected  = status_counts.get("Rejected", 0)
    interview = status_counts.get("Interview", 0)
    offer     = status_counts.get("Offer", 0)
    total     = len(jobs)

    # Response rate = (interview + offer) / applied * 100
    response_rate = round((interview + offer) / applied * 100, 1) if applied > 0 else 0

    # Source breakdown (from tracker)
    source_counts = Counter((j.get("Source") or "Unknown") for j in jobs)

    # Region breakdown
    region_counts = Counter((j.get("Region") or "Unknown") for j in jobs)

    # Jobs found per day (last 30 days) — from tracker
    day_counts: defaultdict = defaultdict(int)
    for j in jobs:
        df = j.get("Date Found", "")
        if df:
            try:
                day = str(df)[:10]
                day_counts[day] += 1
            except Exception:
                pass

    # Fill in missing days (last 30)
    all_days = []
    all_day_counts = []
    for i in range(29, -1, -1):
        d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        all_days.append(d)
        all_day_counts.append(day_counts.get(d, 0))

    # Score distribution (from scored_jobs)
    score_dist = [0] * 11   # index = score (0–10)
    for s in scored:
        sc = int(s.get("score", 0) or 0)
        if 0 <= sc <= 10:
            score_dist[sc] += 1
    score_labels = [str(i) for i in range(1, 11)]
    score_values = score_dist[1:]

    # Avg ATS score (from scored_jobs — proxy via relevance score of compatible jobs)
    compat = [s for s in scored if int(s.get("score", 0) or 0) >= 7]
    avg_score = round(sum(int(s.get("score", 0)) for s in compat) / len(compat), 1) if compat else 0

    # ATS score trend not stored yet — use avg relevance score as proxy
    ats_display = f"{int(avg_score * 10)}%" if avg_score else "N/A"

    # Top 8 companies in tracker
    company_counts = Counter((j.get("Company") or "?") for j in jobs)
    top_companies = company_counts.most_common(8)

    # Recent applications (last 10 Applied rows)
    recent = [j for j in jobs if (j.get("Status") or "") == "Applied"][-10:]

    # Merge any pending dashboard status overrides (written directly from the dashboard UI)
    status_overrides = _load_statuses()

    # All jobs for the full table (capped at 200 for page performance)
    all_jobs_table = []
    for j in reversed(jobs[-200:]):   # most recent first
        job_id = str(j.get("ID") or "")
        status = status_overrides.get(job_id) or str(j.get("Status") or "Waiting to apply")
        all_jobs_table.append({
            "id":       job_id,
            "date":     str(j.get("Date Found") or "")[:10],
            "source":   str(j.get("Source") or ""),
            "company":  str(j.get("Company") or ""),
            "title":    str(j.get("Job Title") or ""),
            "location": str(j.get("Location") or ""),
            "region":   str(j.get("Region") or ""),
            "status":   status,
            "url":      str(j.get("Job URL") or ""),
        })

    # Budget
    budget_pct = 0
    budget_spent = 0
    budget_total = 3.0
    if token_data:
        budget_spent = token_data.get("cost_usd", 0)
        budget_total = token_data.get("budget_usd", 3.0)
        budget_pct = round(budget_spent / budget_total * 100, 1) if budget_total else 0

    # Jobs found this week
    week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    found_this_week = sum(
        1 for j in jobs
        if str(j.get("Date Found", "") or "")[:10] >= week_ago
    )

    return {
        "total": total,
        "applied": applied,
        "waiting": waiting,
        "rejected": rejected,
        "interview": interview,
        "offer": offer,
        "found_this_week": found_this_week,
        "response_rate": response_rate,
        "avg_score": avg_score,
        "ats_display": ats_display,
        "budget_pct": budget_pct,
        "budget_spent": round(budget_spent, 4),
        "budget_total": budget_total,
        "source_labels": list(source_counts.keys()),
        "source_values": list(source_counts.values()),
        "region_labels": list(region_counts.keys()),
        "region_values": list(region_counts.values()),
        "day_labels": all_days,
        "day_values": all_day_counts,
        "score_labels": score_labels,
        "score_values": score_values,
        "top_companies": top_companies,
        "recent": recent,
        "advice":             advice,
        "rejection_analysis": rejection_analysis,
        "all_jobs":           all_jobs_table,
        "generated_at":       now.strftime("%Y-%m-%d %H:%M UTC"),
    }


# ── HTML generation ───────────────────────────────────────────────────────────

_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Career Ops — Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg:       #060b18;
  --bg2:      #0c1326;
  --surface:  rgba(255,255,255,0.038);
  --surface2: rgba(255,255,255,0.065);
  --border:   rgba(255,255,255,0.08);
  --border2:  rgba(255,255,255,0.14);
  --accent:   #6366f1;
  --accent2:  #8b5cf6;
  --green:    #22c55e;
  --yellow:   #f59e0b;
  --red:      #ef4444;
  --blue:     #3b82f6;
  --t1: #f1f5f9;
  --t2: #94a3b8;
  --t3: #475569;
  --r: 14px;
  --r2: 20px;
}

html { scroll-behavior: smooth; }
body {
  font-family: 'Inter', system-ui, sans-serif;
  background: var(--bg);
  color: var(--t1);
  min-height: 100vh;
  line-height: 1.5;
}

/* ── NAV ── */
nav {
  position: sticky; top: 0; z-index: 100;
  display: flex; align-items: center; gap: 16px;
  padding: 0 28px;
  height: 60px;
  background: rgba(6,11,24,0.85);
  backdrop-filter: blur(24px);
  border-bottom: 1px solid var(--border);
}
.nav-logo {
  display: flex; align-items: center; gap: 10px;
  font-weight: 800; font-size: 16px; letter-spacing: -0.3px;
}
.nav-logo-icon {
  width: 32px; height: 32px; border-radius: 8px;
  background: linear-gradient(135deg, var(--accent), var(--accent2));
  display: flex; align-items: center; justify-content: center;
  font-size: 16px;
}
.nav-spacer { flex: 1; }
.nav-badge {
  display: flex; align-items: center; gap: 6px;
  padding: 4px 12px; border-radius: 999px;
  background: rgba(34,197,94,0.12); border: 1px solid rgba(34,197,94,0.25);
  font-size: 12px; font-weight: 600; color: var(--green);
}
.nav-badge::before { content: ''; width:6px; height:6px; border-radius:50%; background:var(--green); animation: pulse 2s infinite; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
.nav-updated { font-size: 12px; color: var(--t3); }

/* ── MAIN ── */
main { max-width: 1400px; margin: 0 auto; padding: 28px 24px 60px; }

/* ── HERO CARDS ── */
.hero-grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 16px;
  margin-bottom: 24px;
}
@media(max-width:1100px){ .hero-grid{ grid-template-columns: repeat(3,1fr); } }
@media(max-width:700px){  .hero-grid{ grid-template-columns: repeat(2,1fr); } }

.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r2);
  padding: 22px 24px;
  transition: border-color .2s, transform .2s;
}
.card:hover { border-color: var(--border2); transform: translateY(-1px); }

.stat-card .label {
  font-size: 11px; font-weight: 600; letter-spacing: .08em;
  text-transform: uppercase; color: var(--t3); margin-bottom: 10px;
}
.stat-card .value {
  font-size: 38px; font-weight: 800; letter-spacing: -1.5px;
  line-height: 1;
  background: linear-gradient(135deg, var(--t1) 60%, var(--t2));
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
}
.stat-card .sub {
  font-size: 12px; color: var(--t3); margin-top: 8px;
}
.stat-card .sub .up   { color: var(--green); font-weight: 600; }
.stat-card .sub .down { color: var(--red);   font-weight: 600; }
.stat-card .sub .neu  { color: var(--yellow); font-weight: 600; }

/* accent card variants */
.card-accent  { border-color: rgba(99,102,241,.35); background: rgba(99,102,241,.06); }
.card-green   { border-color: rgba(34,197,94,.3);   background: rgba(34,197,94,.05); }
.card-yellow  { border-color: rgba(245,158,11,.3);  background: rgba(245,158,11,.05); }

/* ── SECTION TITLES ── */
.section-title {
  font-size: 13px; font-weight: 700; letter-spacing: .07em;
  text-transform: uppercase; color: var(--t2);
  margin-bottom: 16px; display: flex; align-items: center; gap: 8px;
}
.section-title::before {
  content: '';
  display: inline-block; width: 3px; height: 14px; border-radius: 2px;
  background: linear-gradient(to bottom, var(--accent), var(--accent2));
}

/* ── GRID LAYOUTS ── */
.row { display: grid; gap: 16px; margin-bottom: 20px; }
.row-3 { grid-template-columns: 1fr 1fr 1fr; }
.row-2 { grid-template-columns: 1fr 1fr; }
.row-21{ grid-template-columns: 2fr 1fr; }
.row-12{ grid-template-columns: 1fr 2fr; }
@media(max-width:900px){ .row-3,.row-2,.row-21,.row-12 { grid-template-columns: 1fr; } }

/* ── FUNNEL ── */
.funnel { display: flex; flex-direction: column; gap: 8px; }
.funnel-step {
  display: flex; align-items: center; gap: 12px;
}
.funnel-bar-wrap { flex: 1; height: 32px; background: rgba(255,255,255,.05); border-radius: 8px; overflow: hidden; position: relative; }
.funnel-bar {
  height: 100%; border-radius: 8px;
  display: flex; align-items: center; justify-content: flex-end;
  padding-right: 10px;
  font-size: 12px; font-weight: 700; color: rgba(255,255,255,.9);
  transition: width 1s cubic-bezier(.4,0,.2,1);
  min-width: 32px;
}
.funnel-label { font-size: 12px; font-weight: 600; color: var(--t2); width: 100px; text-align: right; }
.funnel-num   { font-size: 12px; font-weight: 700; width: 36px; text-align: right; color: var(--t1); }

/* ── CHART CONTAINERS ── */
.chart-wrap { position: relative; height: 220px; }
.chart-wrap-sm { position: relative; height: 180px; }
.chart-wrap-lg { position: relative; height: 260px; }

/* ── TABLE ── */
.dash-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.dash-table th {
  padding: 10px 12px;
  font-size: 11px; font-weight: 700; letter-spacing: .05em;
  text-transform: uppercase; color: var(--t3);
  border-bottom: 1px solid var(--border);
  text-align: left;
}
.dash-table td {
  padding: 11px 12px;
  border-bottom: 1px solid rgba(255,255,255,.04);
  color: var(--t2);
}
.dash-table td:first-child { color: var(--t1); font-weight: 500; }
.dash-table tr:last-child td { border-bottom: none; }
.dash-table tr:hover td { background: rgba(255,255,255,.02); }

/* ── STATUS BADGE ── */
.badge {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 3px 9px; border-radius: 999px;
  font-size: 11px; font-weight: 600;
}
.badge-applied   { background:rgba(34,197,94,.12);  color:var(--green); }
.badge-waiting   { background:rgba(245,158,11,.12); color:var(--yellow); }
.badge-rejected  { background:rgba(239,68,68,.12);  color:var(--red); }
.badge-interview { background:rgba(99,102,241,.15); color:#818cf8; }
.badge-offer     { background:rgba(20,184,166,.15); color:#2dd4bf; }

/* ── ADVICE PANEL ── */
.advice-panel {
  background: linear-gradient(135deg, rgba(99,102,241,.08), rgba(139,92,246,.05));
  border: 1px solid rgba(99,102,241,.2);
  border-radius: var(--r2);
  padding: 24px 28px;
  margin-top: 8px;
}
.advice-header {
  display: flex; align-items: center; gap: 10px; margin-bottom: 16px;
}
.advice-icon {
  width: 36px; height: 36px; border-radius: 10px;
  background: linear-gradient(135deg, var(--accent), var(--accent2));
  display: flex; align-items: center; justify-content: center; font-size: 18px;
}
.advice-title  { font-weight: 700; font-size: 16px; }
.advice-sub    { font-size: 12px; color: var(--t3); margin-top: 2px; }
.advice-body   { font-size: 13.5px; color: var(--t2); line-height: 1.75; }
.advice-body b { color: var(--t1); }
.advice-body ul { padding-left: 20px; }
.advice-body li { margin-bottom: 6px; }
.advice-empty  { color: var(--t3); font-size: 13px; font-style: italic; }

/* ── BUDGET BAR ── */
.budget-bar-wrap { height: 6px; background: rgba(255,255,255,.07); border-radius: 3px; margin-top: 10px; overflow: hidden; }
.budget-bar { height: 100%; border-radius: 3px; transition: width 1s ease; }

/* ── SCORE BADGE ── */
.score-pill {
  display: inline-block; padding: 1px 8px; border-radius: 999px;
  font-size: 11px; font-weight: 700;
}
.score-hi  { background:rgba(34,197,94,.15);  color:var(--green); }
.score-mid { background:rgba(245,158,11,.15); color:var(--yellow); }
.score-lo  { background:rgba(239,68,68,.15);  color:var(--red); }

/* ── GMAIL STRIP ── */
.gmail-strip {
  background: rgba(59,130,246,.06); border: 1px solid rgba(59,130,246,.18);
  border-radius: 12px; padding: 14px 20px;
  display: flex; align-items: center; gap: 12px;
  font-size: 13px; color: var(--t2);
  margin-bottom: 20px;
}
.gmail-strip strong { color: var(--t1); }
.gmail-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--blue); }

/* ── FOOTER ── */
footer {
  text-align: center; padding: 32px 0 16px;
  font-size: 12px; color: var(--t3);
  border-top: 1px solid var(--border); margin-top: 48px;
}
footer a { color: var(--t2); text-decoration: none; }
footer a:hover { color: var(--t1); }

/* ── FULL OFFERS TABLE ── */
.offers-table-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; }
.offers-table {
  width: 100%; border-collapse: collapse;
  font-size: 12.5px; min-width: 700px;
}
.offers-table th {
  padding: 10px 10px; font-size: 10px; font-weight: 700;
  letter-spacing: .06em; text-transform: uppercase;
  color: var(--t3); border-bottom: 1px solid var(--border);
  text-align: left; white-space: nowrap;
  cursor: pointer; user-select: none;
}
.offers-table th:hover { color: var(--t1); }
.offers-table th.sort-asc::after  { content: " ↑"; color: var(--accent); }
.offers-table th.sort-desc::after { content: " ↓"; color: var(--accent); }
.offers-table td {
  padding: 10px 10px;
  border-bottom: 1px solid rgba(255,255,255,.03);
  color: var(--t2); vertical-align: middle;
}
.offers-table td.col-title { color: var(--t1); font-weight: 500; max-width: 200px; }
.offers-table td.col-company { max-width: 140px; }
.offers-table tr:hover td { background: rgba(255,255,255,.025); }
.offers-table tr:last-child td { border-bottom: none; }

.status-select {
  background: transparent; border: 1px solid var(--border);
  color: var(--t2); border-radius: 6px; padding: 3px 6px;
  font-size: 11px; font-weight: 600; cursor: pointer;
  outline: none;
}
.status-select:focus { border-color: var(--accent); }
.status-select option { background: #0c1326; }

/* ── TOAST ── */
.toast {
  position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
  background: rgba(30,38,60,0.96); border: 1px solid var(--border2);
  backdrop-filter: blur(16px);
  padding: 12px 22px; border-radius: 10px;
  font-size: 13px; color: var(--t1);
  z-index: 9999; display: none;
  box-shadow: 0 8px 32px rgba(0,0,0,.5);
}
.toast.show { display: block; animation: slideUp .25s ease; }
@keyframes slideUp { from{transform:translateX(-50%) translateY(12px);opacity:0} to{transform:translateX(-50%) translateY(0);opacity:1} }

/* ── SEARCH BAR ── */
.table-toolbar {
  display: flex; align-items: center; gap: 12px;
  margin-bottom: 14px; flex-wrap: wrap;
}
.search-input {
  flex: 1; min-width: 180px; max-width: 340px;
  background: var(--surface); border: 1px solid var(--border);
  color: var(--t1); border-radius: 8px;
  padding: 8px 14px; font-size: 13px; outline: none;
}
.search-input:focus { border-color: var(--accent); }
.filter-select {
  background: var(--surface); border: 1px solid var(--border);
  color: var(--t2); border-radius: 8px;
  padding: 8px 10px; font-size: 12px; cursor: pointer; outline: none;
}
.filter-select:focus { border-color: var(--accent); }
.table-count { font-size: 12px; color: var(--t3); margin-left: auto; }

/* ── COMMAND MODAL ── */
.cmd-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,.6);
  backdrop-filter: blur(8px); z-index: 1000;
  display: none; align-items: center; justify-content: center;
}
.cmd-overlay.show { display: flex; }
.cmd-modal {
  background: var(--bg2); border: 1px solid var(--border2);
  border-radius: 16px; padding: 28px 32px; max-width: 480px; width: 92%;
}
.cmd-modal h3 { font-size: 16px; font-weight: 700; margin-bottom: 8px; }
.cmd-modal p  { font-size: 13px; color: var(--t3); margin-bottom: 16px; }
.cmd-box {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; padding: 12px 16px;
  font-family: monospace; font-size: 14px; color: var(--t1);
  word-break: break-all; margin-bottom: 18px;
}
.btn { display: inline-flex; align-items: center; gap: 6px;
  padding: 9px 18px; border-radius: 8px; font-size: 13px;
  font-weight: 600; cursor: pointer; border: none;
  transition: opacity .15s;
}
.btn:hover { opacity: .85; }
.btn-primary { background: var(--accent); color: #fff; }
.btn-ghost   { background: var(--surface); color: var(--t2);
               border: 1px solid var(--border); }
.btn-row { display: flex; gap: 10px; }

/* ── REJECTION PANEL ── */
.rejection-panel {
  background: linear-gradient(135deg, rgba(239,68,68,.06), rgba(245,158,11,.04));
  border: 1px solid rgba(239,68,68,.2);
  border-radius: var(--r2); padding: 24px 28px; margin-top: 20px;
}
.rejection-header { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; }
.rejection-icon {
  width: 36px; height: 36px; border-radius: 10px;
  background: linear-gradient(135deg, #ef4444, #f59e0b);
  display: flex; align-items: center; justify-content: center; font-size: 18px;
}

/* ── MOBILE ── */
@media (max-width: 640px) {
  nav { padding: 0 16px; }
  main { padding: 16px 12px 40px; }
  .hero-grid { grid-template-columns: repeat(2, 1fr); gap: 10px; }
  .hero-grid .card:last-child { grid-column: 1 / -1; }
  .stat-card .value { font-size: 28px; }
  .card { padding: 16px; border-radius: 14px; }
  .chart-wrap { height: 180px; }
  .chart-wrap-lg { height: 200px; }
  .chart-wrap-sm { height: 140px; }
  .row-3, .row-2, .row-21, .row-12 { grid-template-columns: 1fr; }
  .nav-updated { display: none; }
  .advice-panel, .rejection-panel { padding: 16px; }
  .table-toolbar { gap: 8px; }
  .search-input { min-width: 140px; }
  .cmd-modal { padding: 20px; }
}
@media (max-width: 900px) {
  .row-3 { grid-template-columns: 1fr; }
}
</style>
</head>
<body>

<nav>
  <div class="nav-logo">
    <div class="nav-logo-icon">🎯</div>
    Career Ops
  </div>
  <span class="nav-spacer"></span>
  <div class="nav-badge">Live</div>
  <span class="nav-updated">Updated: __GENERATED_AT__</span>
</nav>

<main>

<!-- ── Hero stats ── -->
<div class="hero-grid">

  <div class="card stat-card card-accent">
    <div class="label">Found This Week</div>
    <div class="value">__FOUND_WEEK__</div>
    <div class="sub"><span class="up">__TOTAL__ total</span> across all sources</div>
  </div>

  <div class="card stat-card card-green">
    <div class="label">Applied</div>
    <div class="value">__APPLIED__</div>
    <div class="sub">
      <span class="__RATE_CLASS__">__APPLY_RATE__%</span> of all found
    </div>
  </div>

  <div class="card stat-card">
    <div class="label">Awaiting Response</div>
    <div class="value">__WAITING__</div>
    <div class="sub">
      <span class="__RESP_CLASS__">__RESPONSE_RATE__%</span> response rate
    </div>
  </div>

  <div class="card stat-card">
    <div class="label">Avg Relevance Score</div>
    <div class="value">__AVG_SCORE__</div>
    <div class="sub">/ 10 &nbsp;·&nbsp; last 30 days</div>
  </div>

  <div class="card stat-card card-yellow">
    <div class="label">Claude Budget</div>
    <div class="value">__BUDGET_PCT__%</div>
    <div class="sub">$__BUDGET_SPENT__ / $__BUDGET_TOTAL__ today</div>
    <div class="budget-bar-wrap">
      <div class="budget-bar" style="width:__BUDGET_PCT__%; background:__BUDGET_COLOR__;"></div>
    </div>
  </div>

</div>

<!-- ── Gmail integration strip ── -->
__GMAIL_STRIP__

<!-- ── Row 1: Funnel + Status Donut + Interviews ── -->
<div class="row row-3">

  <div class="card">
    <div class="section-title">Application Funnel</div>
    <div class="funnel" id="funnel-el"></div>
  </div>

  <div class="card">
    <div class="section-title">Status Breakdown</div>
    <div class="chart-wrap"><canvas id="statusChart"></canvas></div>
  </div>

  <div class="card">
    <div class="section-title">Score Distribution</div>
    <div class="chart-wrap"><canvas id="scoreChart"></canvas></div>
  </div>

</div>

<!-- ── Row 2: Timeline ── -->
<div class="card" style="margin-bottom:20px">
  <div class="section-title">Daily Activity — Last 30 Days</div>
  <div class="chart-wrap-lg"><canvas id="timelineChart"></canvas></div>
</div>

<!-- ── Row 3: Source + Region ── -->
<div class="row row-2">

  <div class="card">
    <div class="section-title">Jobs by Source</div>
    <div class="chart-wrap"><canvas id="sourceChart"></canvas></div>
  </div>

  <div class="card">
    <div class="section-title">Jobs by Region</div>
    <div class="chart-wrap"><canvas id="regionChart"></canvas></div>
  </div>

</div>

<!-- ── Row 4: Top companies + Recent ── -->
<div class="row row-2">

  <div class="card">
    <div class="section-title">Top Companies</div>
    <table class="dash-table">
      <thead><tr><th>Company</th><th>Jobs Found</th><th></th></tr></thead>
      <tbody id="companies-tbody"></tbody>
    </table>
  </div>

  <div class="card">
    <div class="section-title">Recent Applications</div>
    <table class="dash-table">
      <thead><tr><th>Role</th><th>Company</th><th>Status</th></tr></thead>
      <tbody id="recent-tbody"></tbody>
    </table>
  </div>

</div>

<!-- ── All Offers Table ── -->
<div class="card" style="margin-bottom:20px">
  <div class="section-title">All Offers</div>

  <div class="table-toolbar">
    <input class="search-input" id="offer-search" type="text" placeholder="Search company, title, location…">
    <select class="filter-select" id="status-filter">
      <option value="">All statuses</option>
      <option value="Waiting to apply">Waiting</option>
      <option value="Applied">Applied</option>
      <option value="Interview">Interview</option>
      <option value="Offer">Offer</option>
      <option value="Rejected">Rejected</option>
    </select>
    <select class="filter-select" id="source-filter">
      <option value="">All sources</option>
    </select>
    <span class="table-count" id="offer-count"></span>
  </div>

  <div class="offers-table-wrap">
    <table class="offers-table" id="offers-table">
      <thead>
        <tr>
          <th data-col="date">Date</th>
          <th data-col="title">Role</th>
          <th data-col="company">Company</th>
          <th data-col="location">Location</th>
          <th data-col="source">Source</th>
          <th data-col="status">Status</th>
          <th>Update</th>
          <th>Link</th>
        </tr>
      </thead>
      <tbody id="offers-tbody"></tbody>
    </table>
  </div>
  <div id="offers-show-more" style="text-align:center;margin-top:14px;display:none">
    <button class="btn btn-ghost" onclick="showAllOffers()">Show all offers</button>
  </div>
</div>

<!-- ── AI Advice Panel ── -->
<div class="advice-panel">
  <div class="advice-header">
    <div class="advice-icon">🧠</div>
    <div>
      <div class="advice-title">Claude — Weekly Skills &amp; Strategy Analysis</div>
      <div class="advice-sub">Generated every Monday from scored job data</div>
    </div>
  </div>
  <div class="advice-body" id="advice-body"></div>
</div>

<!-- ── Rejection Analysis Panel ── -->
<div class="rejection-panel" id="rejection-panel" style="display:none">
  <div class="rejection-header">
    <div class="rejection-icon">📉</div>
    <div>
      <div class="advice-title">Rejection Pattern Analysis</div>
      <div class="advice-sub">Auto-generated when ≥5 rejections accumulate</div>
    </div>
  </div>
  <div class="advice-body" id="rejection-body"></div>
</div>

</main>

<!-- ── Saving spinner (shown while GitHub API call is in flight) ── -->
<div class="cmd-overlay" id="saving-overlay" style="display:none;align-items:center;justify-content:center">
  <div class="cmd-modal" style="text-align:center;padding:32px 40px">
    <div style="font-size:28px;margin-bottom:12px">💾</div>
    <div style="font-size:15px;font-weight:600" id="saving-msg">Saving…</div>
  </div>
</div>

<!-- ── Toast ── -->
<div class="toast" id="toast"></div>

<footer>
  Built with <a href="https://github.com/aaitdads16/career-ops" target="_blank">career-ops</a>
  &nbsp;·&nbsp; Generated __GENERATED_AT__
  &nbsp;·&nbsp; <a href="https://github.com/aaitdads16/career-ops/blob/main/data/tracker.xlsx">Download Tracker</a>
</footer>

<script>
// ── Embedded data ────────────────────────────────────────────────────────────
const DATA = __DATA_JSON__;

// ── Funnel ───────────────────────────────────────────────────────────────────
(function buildFunnel() {
  const steps = [
    { label: 'Found',      value: DATA.total,     color: '#6366f1' },
    { label: 'Applied',    value: DATA.applied,   color: '#22c55e' },
    { label: 'Waiting',    value: DATA.waiting,   color: '#f59e0b' },
    { label: 'Interview',  value: DATA.interview, color: '#3b82f6' },
    { label: 'Offer',      value: DATA.offer,     color: '#2dd4bf' },
  ];
  const max = Math.max(DATA.total, 1);
  const el = document.getElementById('funnel-el');
  steps.forEach(s => {
    const pct = Math.max(s.value / max * 100, s.value > 0 ? 6 : 0);
    el.innerHTML += `
      <div class="funnel-step">
        <div class="funnel-label">${s.label}</div>
        <div class="funnel-bar-wrap">
          <div class="funnel-bar" style="width:${pct}%;background:${s.color}">
            ${s.value > 0 ? s.value : ''}
          </div>
        </div>
        <div class="funnel-num">${s.value}</div>
      </div>`;
  });
})();

// ── Chart defaults ────────────────────────────────────────────────────────────
Chart.defaults.color = '#94a3b8';
Chart.defaults.borderColor = 'rgba(255,255,255,0.07)';
Chart.defaults.font.family = "'Inter', system-ui, sans-serif";

// ── Status donut ──────────────────────────────────────────────────────────────
new Chart(document.getElementById('statusChart'), {
  type: 'doughnut',
  data: {
    labels: ['Applied', 'Waiting', 'Rejected', 'Interview', 'Offer'],
    datasets: [{
      data: [DATA.applied, DATA.waiting, DATA.rejected, DATA.interview, DATA.offer],
      backgroundColor: ['#22c55e','#f59e0b','#ef4444','#6366f1','#2dd4bf'],
      borderWidth: 2,
      borderColor: '#060b18',
      hoverOffset: 6,
    }]
  },
  options: {
    cutout: '68%',
    plugins: {
      legend: { position: 'right', labels: { boxWidth: 10, padding: 12, font: { size: 12 } } }
    },
    animation: { animateRotate: true, duration: 1200 }
  }
});

// ── Score distribution ────────────────────────────────────────────────────────
new Chart(document.getElementById('scoreChart'), {
  type: 'bar',
  data: {
    labels: DATA.score_labels,
    datasets: [{
      label: 'Jobs',
      data: DATA.score_values,
      backgroundColor: DATA.score_labels.map((_, i) => {
        const v = parseInt(DATA.score_labels[i]);
        return v >= 8 ? 'rgba(34,197,94,0.75)' : v >= 6 ? 'rgba(245,158,11,0.65)' : 'rgba(239,68,68,0.55)';
      }),
      borderRadius: 6,
      borderSkipped: false,
    }]
  },
  options: {
    plugins: { legend: { display: false } },
    scales: {
      y: { beginAtZero: true, ticks: { stepSize: 1 } },
      x: { grid: { display: false } }
    }
  }
});

// ── Timeline ──────────────────────────────────────────────────────────────────
new Chart(document.getElementById('timelineChart'), {
  type: 'line',
  data: {
    labels: DATA.day_labels.map(d => d.slice(5)),  // MM-DD
    datasets: [{
      label: 'Jobs Found',
      data: DATA.day_values,
      borderColor: '#6366f1',
      backgroundColor: 'rgba(99,102,241,0.1)',
      borderWidth: 2.5,
      pointRadius: 3,
      pointBackgroundColor: '#6366f1',
      tension: 0.4,
      fill: true,
    }]
  },
  options: {
    plugins: { legend: { display: false } },
    scales: {
      y: { beginAtZero: true, ticks: { stepSize: 1 } },
      x: { ticks: { maxTicksLimit: 15 } }
    },
    animation: { duration: 1500 }
  }
});

// ── Source bar ────────────────────────────────────────────────────────────────
const srcColors = { 'LinkedIn':'#0a66c2','Indeed':'#2164f3','Glassdoor':'#0caa41','Wellfound':'#f26522','Google Jobs':'#ea4335' };
new Chart(document.getElementById('sourceChart'), {
  type: 'bar',
  data: {
    labels: DATA.source_labels,
    datasets: [{
      label: 'Jobs',
      data: DATA.source_values,
      backgroundColor: DATA.source_labels.map(s => srcColors[s] || '#6366f1'),
      borderRadius: 8,
      borderSkipped: false,
    }]
  },
  options: {
    indexAxis: 'y',
    plugins: { legend: { display: false } },
    scales: {
      x: { beginAtZero: true, ticks: { stepSize: 1 } },
      y: { grid: { display: false } }
    }
  }
});

// ── Region bar ────────────────────────────────────────────────────────────────
const regEmoji = { 'Europe':'🇪🇺','Asia':'🌏','USA_Canada':'🇺🇸','South_America':'🌎','Middle_East':'🌍' };
new Chart(document.getElementById('regionChart'), {
  type: 'bar',
  data: {
    labels: DATA.region_labels.map(r => (regEmoji[r]||'🌐') + ' ' + r.replace('_',' ')),
    datasets: [{
      label: 'Jobs',
      data: DATA.region_values,
      backgroundColor: ['rgba(99,102,241,0.8)','rgba(139,92,246,0.75)','rgba(59,130,246,0.75)','rgba(20,184,166,0.7)','rgba(245,158,11,0.7)'],
      borderRadius: 8,
      borderSkipped: false,
    }]
  },
  options: {
    indexAxis: 'y',
    plugins: { legend: { display: false } },
    scales: {
      x: { beginAtZero: true, ticks: { stepSize: 1 } },
      y: { grid: { display: false } }
    }
  }
});

// ── Top companies table ───────────────────────────────────────────────────────
(function buildCompanies() {
  const tbody = document.getElementById('companies-tbody');
  const maxC  = Math.max(...DATA.top_companies.map(c => c[1]), 1);
  DATA.top_companies.forEach(([co, cnt], i) => {
    const pct = Math.round(cnt / maxC * 100);
    tbody.innerHTML += `
      <tr>
        <td>${co}</td>
        <td>${cnt}</td>
        <td><div style="height:5px;border-radius:3px;background:rgba(99,102,241,0.2);overflow:hidden">
          <div style="height:100%;width:${pct}%;background:linear-gradient(90deg,#6366f1,#8b5cf6);border-radius:3px"></div>
        </div></td>
      </tr>`;
  });
  if (!DATA.top_companies.length)
    tbody.innerHTML = '<tr><td colspan="3" style="color:var(--t3);font-style:italic">No data yet</td></tr>';
})();

// ── Recent applications table ─────────────────────────────────────────────────
(function buildRecent() {
  const tbody = document.getElementById('recent-tbody');
  const statusClass = s => {
    if (s === 'Applied')           return 'badge-applied';
    if (s === 'Interview')         return 'badge-interview';
    if (s === 'Offer')             return 'badge-offer';
    if (s === 'Rejected')          return 'badge-rejected';
    return 'badge-waiting';
  };
  DATA.recent.slice().reverse().forEach(j => {
    const s = j['Status'] || 'Waiting to apply';
    tbody.innerHTML += `
      <tr>
        <td>${(j['Job Title']||'').slice(0,32)}</td>
        <td>${(j['Company']||'').slice(0,22)}</td>
        <td><span class="badge ${statusClass(s)}">${s}</span></td>
      </tr>`;
  });
  if (!DATA.recent.length)
    tbody.innerHTML = '<tr><td colspan="3" style="color:var(--t3);font-style:italic">No applications yet</td></tr>';
})();

// ── Advice panel ──────────────────────────────────────────────────────────────
function mdToHtml(text) {
  return text
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/\*\*(.*?)\*\*/g,'<b>$1</b>')
    .replace(/^[-•]\s+(.+)$/gm,'<li>$1</li>')
    .replace(/(<li>[^]*?<\/li>\n?)+/g, s => '<ul>'+s+'</ul>');
}
(function buildAdvice() {
  const el = document.getElementById('advice-body');
  if (DATA.advice) {
    el.innerHTML = mdToHtml(DATA.advice);
  } else {
    el.innerHTML = '<span class="advice-empty">Weekly analysis appears every Monday once ≥20 jobs are scored.</span>';
  }
})();

// ── Rejection analysis panel ──────────────────────────────────────────────────
(function buildRejection() {
  if (!DATA.rejection_analysis) return;
  document.getElementById('rejection-panel').style.display = '';
  document.getElementById('rejection-body').innerHTML = mdToHtml(DATA.rejection_analysis);
})();

// ── All Offers Table ──────────────────────────────────────────────────────────
(function buildOffersTable() {
  const jobs = DATA.all_jobs || [];
  const STATUSES = ['Waiting to apply','Applied','Interview','Offer','Rejected'];
  const STATUS_CLASS = {
    'Applied':'badge-applied','Interview':'badge-interview',
    'Offer':'badge-offer','Rejected':'badge-rejected'
  };
  const SRC_EMOJI = {
    'LinkedIn':'🔷','Indeed':'🔵','Glassdoor':'🟢',
    'Google Jobs':'🔴','RemoteOK':'🟠','RemoteOk':'🟠'
  };

  // Populate source filter
  const srcFilter = document.getElementById('source-filter');
  const srcSet = [...new Set(jobs.map(j => j.source).filter(Boolean))].sort();
  srcSet.forEach(s => {
    const o = document.createElement('option'); o.value = s; o.textContent = s;
    srcFilter.appendChild(o);
  });

  let sortCol = 'date', sortDir = -1;
  let visibleCount = 50;
  let pendingJob = null, pendingStatus = null;

  function filtered() {
    const q = (document.getElementById('offer-search').value||'').toLowerCase();
    const st = document.getElementById('status-filter').value;
    const sc = document.getElementById('source-filter').value;
    return jobs.filter(j => {
      if (st && j.status !== st) return false;
      if (sc && j.source !== sc) return false;
      if (q && !(
        (j.title||'').toLowerCase().includes(q) ||
        (j.company||'').toLowerCase().includes(q) ||
        (j.location||'').toLowerCase().includes(q) ||
        (j.id||'').toLowerCase().includes(q)
      )) return false;
      return true;
    });
  }

  function sorted(arr) {
    return [...arr].sort((a,b) => {
      const av = (a[sortCol]||'').toString();
      const bv = (b[sortCol]||'').toString();
      return av < bv ? sortDir : av > bv ? -sortDir : 0;
    });
  }

  function render() {
    const rows = sorted(filtered());
    const shown = rows.slice(0, visibleCount);
    document.getElementById('offer-count').textContent = `${rows.length} offer${rows.length!==1?'s':''}`;
    document.getElementById('offers-show-more').style.display =
      rows.length > visibleCount ? '' : 'none';

    const tbody = document.getElementById('offers-tbody');
    tbody.innerHTML = shown.map(j => {
      const sClass = STATUS_CLASS[j.status] || 'badge-waiting';
      const emoji  = SRC_EMOJI[j.source] || '⚪️';
      const opts   = STATUSES.map(s =>
        `<option value="${s}"${s===j.status?' selected':''}>${s}</option>`
      ).join('');
      const applyLink = j.url && j.url !== '–'
        ? `<a href="${j.url}" target="_blank" rel="noopener" style="color:var(--accent);font-size:12px">Apply →</a>`
        : '<span style="color:var(--t3);font-size:11px">–</span>';
      return `<tr data-id="${j.id}">
        <td style="white-space:nowrap;font-size:11px">${j.date}</td>
        <td class="col-title" title="${j.title}">${(j.title||'').slice(0,38)}${j.title.length>38?'…':''}</td>
        <td class="col-company" title="${j.company}">${(j.company||'').slice(0,22)}${(j.company||'').length>22?'…':''}</td>
        <td style="font-size:11px;max-width:120px">${(j.location||'').slice(0,20)}</td>
        <td style="white-space:nowrap">${emoji} ${j.source||''}</td>
        <td><span class="badge ${sClass}">${j.status||'Waiting'}</span></td>
        <td>
          <select class="status-select" onchange="requestStatusUpdate('${j.id}',this.value,this)">
            ${opts}
          </select>
        </td>
        <td>${applyLink}</td>
      </tr>`;
    }).join('');
  }

  // ── Direct GitHub API status update ─────────────────────────────────────────
  const GH_TOKEN = '__DASHBOARD_PAT__';
  const GH_REPO  = '__GITHUB_REPO__';
  const GH_FILE  = 'data/statuses.json';

  window.requestStatusUpdate = async function(jobId, newStatus, sel) {
    const oldStatus = (jobs.find(j => j.id === jobId) || {}).status || '';
    if (newStatus === oldStatus) return;

    // No PAT injected (local dev) — show the old Telegram fallback toast
    if (!GH_TOKEN || GH_TOKEN === '__DASHBOARD_PAT__') {
      sel.value = oldStatus;
      showToast('⚠️ No PAT configured — use /setstatus ' + jobId + ' ' + newStatus + ' in Telegram');
      return;
    }

    sel.disabled = true;
    const overlay = document.getElementById('saving-overlay');
    const msg     = document.getElementById('saving-msg');
    overlay.style.display = 'flex';
    msg.textContent = 'Saving…';

    try {
      // 1. Fetch current file + SHA
      const getResp = await fetch(
        `https://api.github.com/repos/${GH_REPO}/contents/${GH_FILE}`,
        { headers: { Authorization: `token ${GH_TOKEN}`, Accept: 'application/vnd.github.v3+json' } }
      );
      if (!getResp.ok) throw new Error(`GET ${getResp.status}`);
      const fileData = await getResp.json();
      const sha = fileData.sha;
      let current = {};
      try { current = JSON.parse(atob(fileData.content.replace(/\n/g, ''))); } catch(_) {}

      // 2. Merge new status
      current[jobId] = newStatus;
      const encoded = btoa(unescape(encodeURIComponent(JSON.stringify(current, null, 2))));

      // 3. Write back
      const putResp = await fetch(
        `https://api.github.com/repos/${GH_REPO}/contents/${GH_FILE}`,
        {
          method: 'PUT',
          headers: {
            Authorization: `token ${GH_TOKEN}`,
            Accept: 'application/vnd.github.v3+json',
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            message: `status: ${jobId} → ${newStatus} [skip ci]`,
            content: encoded,
            sha: sha,
          }),
        }
      );
      if (!putResp.ok) {
        const e = await putResp.json().catch(() => ({}));
        throw new Error(e.message || `PUT ${putResp.status}`);
      }

      // 4. Update local data + re-render immediately
      const job = jobs.find(j => j.id === jobId);
      if (job) job.status = newStatus;
      render();
      msg.textContent = '✅ Saved!';
      setTimeout(() => { overlay.style.display = 'none'; }, 800);
      showToast(`✅ Status → ${newStatus}`);
    } catch (err) {
      overlay.style.display = 'none';
      sel.value = oldStatus;
      sel.disabled = false;
      showToast('❌ Save failed: ' + err.message);
    } finally {
      sel.disabled = false;
    }
  };

  window.showAllOffers = function() {
    visibleCount = Infinity; render();
  };

  // Sorting
  document.querySelectorAll('.offers-table th[data-col]').forEach(th => {
    th.addEventListener('click', () => {
      const col = th.dataset.col;
      if (sortCol === col) { sortDir *= -1; }
      else { sortCol = col; sortDir = -1; }
      document.querySelectorAll('.offers-table th').forEach(t => {
        t.classList.remove('sort-asc','sort-desc');
      });
      th.classList.add(sortDir === -1 ? 'sort-desc' : 'sort-asc');
      visibleCount = 50; render();
    });
  });

  // Close saving overlay on click (safety escape)
  document.getElementById('saving-overlay').addEventListener('click', e => {
    if (e.target === e.currentTarget) e.currentTarget.style.display = 'none';
  });

  // Search + filter
  ['offer-search','status-filter','source-filter'].forEach(id => {
    document.getElementById(id).addEventListener('input', () => {
      visibleCount = 50; render();
    });
  });

  render();

  // ── Live overlay: fetch statuses.json on load to reflect recent changes ──────
  // Works even before the dashboard HTML is rebuilt (GitHub API writes statuses.json
  // directly; this fetch picks it up within seconds without needing a new deployment).
  (async function liveOverlay() {
    try {
      const r = await fetch(
        'https://raw.githubusercontent.com/__GITHUB_REPO__/main/data/statuses.json?_=' + Date.now(),
        { cache: 'no-store' }
      );
      if (!r.ok) return;
      const ov = await r.json();
      let hit = false;
      for (const [id, st] of Object.entries(ov)) {
        const j = jobs.find(x => x.id === id);
        if (j && j.status !== st) { j.status = st; hit = true; }
      }
      if (hit) render();
    } catch(_) { /* offline or rate-limited — silent fail */ }
  })();
})();

// ── Toast ─────────────────────────────────────────────────────────────────────
function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 3200);
}
</script>
</body>
</html>"""


def _badge_class(status: str) -> str:
    m = {"Applied": "badge-applied", "Interview": "badge-interview",
         "Offer": "badge-offer", "Rejected": "badge-rejected"}
    return m.get(status, "badge-waiting")


def generate_dashboard() -> Path:
    """Generate docs/index.html from current tracker data. Returns output path."""
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    s = _compute_stats()

    total    = max(s["total"], 1)
    apply_rate = round(s["applied"] / total * 100, 1)
    rate_class = "up" if apply_rate >= 30 else "neu" if apply_rate >= 10 else "down"
    resp_class = "up" if s["response_rate"] >= 10 else "neu" if s["response_rate"] > 0 else "down"
    budget_pct = s["budget_pct"]
    budget_color = ("#ef4444" if budget_pct >= 80 else "#f59e0b" if budget_pct >= 50 else "#22c55e")

    gmail_strip = ""  # populated if gmail integration is active
    gmail_status_file = DATA_DIR / "gmail_last_sync.txt"
    if gmail_status_file.exists():
        try:
            last_sync = gmail_status_file.read_text().strip()
            gmail_strip = (
                f'<div class="gmail-strip">'
                f'<div class="gmail-dot"></div>'
                f'<div><strong>Gmail sync active</strong> — application emails automatically update your tracker. '
                f'Last sync: {last_sync}</div></div>'
            )
        except Exception:
            pass

    # Prepare embedded JSON data (safe: only numbers, strings, lists)
    data_json = json.dumps({
        "total":         s["total"],
        "applied":       s["applied"],
        "waiting":       s["waiting"],
        "rejected":      s["rejected"],
        "interview":     s["interview"],
        "offer":         s["offer"],
        "response_rate": s["response_rate"],
        "avg_score":     s["avg_score"],
        "source_labels": s["source_labels"],
        "source_values": s["source_values"],
        "region_labels": s["region_labels"],
        "region_values": s["region_values"],
        "day_labels":    s["day_labels"],
        "day_values":    s["day_values"],
        "score_labels":  s["score_labels"],
        "score_values":  s["score_values"],
        "top_companies": [[co, cnt] for co, cnt in s["top_companies"]],
        "recent":        [
            {
                "Job Title": r.get("Job Title", ""),
                "Company":   r.get("Company", ""),
                "Status":    r.get("Status", ""),
            }
            for r in s["recent"]
        ],
        "advice":             s["advice"],
        "rejection_analysis": s.get("rejection_analysis", ""),
        "all_jobs":           s.get("all_jobs", []),
    }, ensure_ascii=False)

    # PAT injected at build time from DASHBOARD_PAT env var (GitHub Actions secret)
    dashboard_pat  = os.environ.get("DASHBOARD_PAT", "")
    github_repo    = os.environ.get("GITHUB_REPOSITORY", "aaitdads16/career-ops")

    html = _HTML_TEMPLATE
    replacements = {
        "__GENERATED_AT__": s["generated_at"],
        "__FOUND_WEEK__":   str(s["found_this_week"]),
        "__TOTAL__":        str(s["total"]),
        "__APPLIED__":      str(s["applied"]),
        "__WAITING__":      str(s["waiting"]),
        "__APPLY_RATE__":   str(apply_rate),
        "__RATE_CLASS__":   rate_class,
        "__RESPONSE_RATE__": str(s["response_rate"]),
        "__RESP_CLASS__":   resp_class,
        "__AVG_SCORE__":    str(s["avg_score"]),
        "__ATS_DISPLAY__":  s["ats_display"],
        "__BUDGET_PCT__":   str(budget_pct),
        "__BUDGET_SPENT__": str(s["budget_spent"]),
        "__BUDGET_TOTAL__": str(s["budget_total"]),
        "__BUDGET_COLOR__": budget_color,
        "__GMAIL_STRIP__":  gmail_strip,
        "__DATA_JSON__":    data_json,
        "__DASHBOARD_PAT__": dashboard_pat,
        "__GITHUB_REPO__":   github_repo,
    }
    for k, v in replacements.items():
        html = html.replace(k, v)

    OUTPUT.write_text(html, encoding="utf-8")
    logger.info("Dashboard written → %s", OUTPUT)
    return OUTPUT


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
    out = generate_dashboard()
    print(f"✓ Dashboard → {out}")
    print(f"  Open: file://{out}")
