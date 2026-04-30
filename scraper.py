"""
Multi-source internship scraper — Apify-free.

Sources:
  - JobSpy   : LinkedIn · Indeed · Glassdoor · ZipRecruiter  (free, no key)
  - Adzuna   : Official job board API, 15+ countries          (free account)
  - JSearch  : RapidAPI aggregator, global                    (free, 500 req/mo)
  - SerpAPI  : Google Jobs                                    (free, 100 req/mo)
  - RemoteOK : Public JSON API                                (no key needed)
  + scraper_free.py: Arbeitnow · Jobteaser · Internshala
"""

import hashlib
import logging
import random
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set

import requests as _http

from config import (
    ADZUNA_APP_ID,
    ADZUNA_APP_KEY,
    COMPANY_BLACKLIST,
    JOBSPY_HOURS_OLD,
    JOBSPY_RESULTS_PER_CALL,
    JSEARCH_API_KEY,
    JSEARCH_MAX_CALLS_PER_RUN,
    MAX_JOB_AGE_DAYS,
    REGIONS,
    SEARCH_KEYWORDS,
    SERPAPI_KEY,
    SERPAPI_MAX_CALLS_PER_RUN,
)

logger = logging.getLogger(__name__)


# ── Shared helpers ─────────────────────────────────────────────────────────────

_EXCLUDED_LOCATIONS = [
    "france", "paris", "lyon", "marseille", "toulouse", "nice",
    "morocco", "maroc", "casablanca", "rabat",
    "egypt", "nigeria", "kenya", "south africa", "algerie", "algeria",
    "tunisia", "ghana", "senegal", "ethiopia", "tanzania",
    "cameroon", "ivory coast", "côte d'ivoire", "abidjan",
]


def _is_excluded(location: str) -> bool:
    loc = (location or "").lower()
    return any(ex in loc for ex in _EXCLUDED_LOCATIONS)


def _is_blacklisted(company: str) -> bool:
    name = (company or "").lower()
    return any(bl in name for bl in COMPANY_BLACKLIST)


def _parse_date(raw) -> Optional[datetime]:
    if not raw:
        return None
    try:
        if isinstance(raw, datetime):
            return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(raw / 1000 if raw > 1e10 else raw, tz=timezone.utc)
        from dateutil import parser as dp
        dt = dp.parse(str(raw))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _infer_region(location: str) -> str:
    loc = (location or "").lower()
    europe = [
        "london", "berlin", "munich", "amsterdam", "stockholm", "zurich", "barcelona",
        "madrid", "dublin", "brussels", "copenhagen", "oslo", "helsinki", "milan",
        "lisbon", "warsaw", "prague", "vienna", "bucharest", "budapest", "rome",
        "united kingdom", " uk,", "germany", "netherlands", "sweden", "switzerland",
        "spain", "ireland", "belgium", "denmark", "norway", "finland", "italy",
        "portugal", "poland", "austria", "europe",
    ]
    asia = [
        "singapore", "tokyo", "seoul", "hong kong", "bangalore", "mumbai",
        "kuala lumpur", "taipei", "bangkok", "jakarta", "sydney", "melbourne",
        "auckland", "japan", "india", "australia", "south korea", "asia",
    ]
    latam = [
        "são paulo", "sao paulo", "buenos aires", "bogotá", "bogota", "santiago",
        "mexico city", "brazil", "argentina", "colombia", "chile", "mexico",
        "latin america", "south america",
    ]
    me = ["dubai", "abu dhabi", "riyadh", "doha", "middle east", "gulf", "uae", "saudi"]

    for h in europe:
        if h in loc:
            return "Europe"
    for h in asia:
        if h in loc:
            return "Asia"
    for h in latam:
        if h in loc:
            return "South_America"
    for h in me:
        if h in loc:
            return "Middle_East"
    return "USA_Canada"


def _make_fingerprint(title: str, company: str) -> str:
    """Stable hash for deduplicating the same job across sources and runs."""
    key = f"{title.lower().strip()}::{company.lower().strip()}"
    return hashlib.md5(key.encode()).hexdigest()


# ── JobSpy — LinkedIn · Indeed · Glassdoor · ZipRecruiter (no key required) ──

# Key locations per region — kept tight to avoid rate-limiting
# Format: (region_name, location_string, indeed_country_for_jobspy)
_JOBSPY_LOCATIONS = [
    ("Europe",        "London, United Kingdom",  "UK"),
    ("Europe",        "Berlin, Germany",          "Germany"),
    ("Europe",        "Amsterdam, Netherlands",   "Netherlands"),
    ("Europe",        "Zurich, Switzerland",      "Switzerland"),
    ("Europe",        "Stockholm, Sweden",        "Sweden"),
    ("Europe",        "Dublin, Ireland",          "Ireland"),
    ("Europe",        "Barcelona, Spain",         "Spain"),
    ("Europe",        "Warsaw, Poland",           "Poland"),
    ("Asia",          "Singapore",                "Singapore"),
    ("Asia",          "Tokyo, Japan",             "Japan"),
    ("Asia",          "Bangalore, India",         "India"),
    ("Asia",          "Seoul, South Korea",       "South Korea"),
    ("Asia",          "Sydney, Australia",        "Australia"),
    ("USA_Canada",    "New York, NY",             "USA"),
    ("USA_Canada",    "San Francisco, CA",        "USA"),
    ("USA_Canada",    "Boston, MA",               "USA"),
    ("USA_Canada",    "Toronto, Canada",          "Canada"),
    ("South_America", "São Paulo, Brazil",        "Brazil"),
    ("South_America", "Buenos Aires, Argentina",  "Argentina"),
    ("Middle_East",   "Dubai, UAE",               "worldwide"),
]


def _safe_str(val) -> str:
    """Convert a pandas/dict value to str, treating NaN/None as empty string."""
    import math
    if val is None:
        return ""
    try:
        if isinstance(val, float) and math.isnan(val):
            return ""
    except (TypeError, ValueError):
        pass
    return str(val).strip()


def _normalize_jobspy_row(row: dict, region: str) -> Optional[dict]:
    site     = _safe_str(row.get("site")) or "jobspy"
    job_id   = _safe_str(row.get("id")) or _safe_str(row.get("job_url"))
    title    = _safe_str(row.get("title"))
    company  = _safe_str(row.get("company"))
    location = _safe_str(row.get("location"))
    url      = _safe_str(row.get("job_url")) or _safe_str(row.get("job_url_direct"))
    desc     = _safe_str(row.get("description"))
    posted   = row.get("date_posted")  # datetime.date or None from JobSpy

    if not (title and company and url):
        return None
    if _is_excluded(location) or _is_blacklisted(company):
        return None

    # JobSpy returns datetime.date — convert to timezone-aware datetime
    posted_dt = None
    if posted:
        try:
            from datetime import date
            if isinstance(posted, date) and not isinstance(posted, datetime):
                posted_dt = datetime(posted.year, posted.month, posted.day, tzinfo=timezone.utc)
            elif isinstance(posted, datetime):
                posted_dt = posted if posted.tzinfo else posted.replace(tzinfo=timezone.utc)
        except Exception:
            posted_dt = _parse_date(str(posted))

    jid = hashlib.md5((job_id or url).encode()).hexdigest()[:16]
    return {
        "job_id":      f"{site}_{jid}",
        "source":      site.title(),
        "title":       title,
        "company":     company,
        "location":    location,
        "region":      region,
        "url":         url,
        "description": desc[:3000],
        "posted_at":   posted_dt,
        "posted_raw":  str(posted or ""),
        "found_at":    datetime.now(tz=timezone.utc),
    }


def scrape_jobspy(seen_ids: Set[str]) -> Dict[str, List[dict]]:
    """Scrape LinkedIn, Indeed, Glassdoor via JobSpy — completely free, no API key."""
    try:
        from jobspy import scrape_jobs as _jobspy  # type: ignore
    except ImportError:
        logger.warning("JobSpy not installed — skipping. Run: pip install python-jobspy")
        return {r: [] for r in REGIONS}

    # LinkedIn blocks GitHub Actions IP ranges — only use Indeed + Glassdoor in CI
    import os as _os
    _in_ci = _os.getenv("GITHUB_ACTIONS") == "true"
    _sites = ["indeed", "glassdoor"] if _in_ci else ["linkedin", "indeed", "glassdoor"]
    if _in_ci:
        logger.info("  JobSpy: CI detected — LinkedIn excluded (IP blocked by LinkedIn in GH Actions)")

    results: Dict[str, List[dict]] = {r: [] for r in REGIONS}
    seen_in_run: Set[str] = set()
    keywords = list(SEARCH_KEYWORDS[:3])  # top 3 keywords to keep call count manageable

    for region_name, location, country_indeed in _JOBSPY_LOCATIONS:
        for keyword in keywords:
            try:
                df = _jobspy(
                    site_name=_sites,
                    search_term=keyword,
                    location=location,
                    results_wanted=JOBSPY_RESULTS_PER_CALL,
                    hours_old=JOBSPY_HOURS_OLD,
                    country_indeed=country_indeed,
                    linkedin_fetch_description=False,  # skip extra per-job requests
                    verbose=0,
                )
            except Exception as exc:
                logger.warning("  JobSpy [%s] '%s' error: %s", location, keyword, exc)
                time.sleep(3)
                continue

            if df is None or df.empty:
                continue

            new_found = 0
            for _, row in df.iterrows():
                norm = _normalize_jobspy_row(row.to_dict(), region_name)
                if not norm:
                    continue
                jid = norm["job_id"]
                if jid in seen_ids or jid in seen_in_run:
                    continue
                seen_in_run.add(jid)
                results[region_name].append(norm)
                new_found += 1

            logger.info("  JobSpy [%s] '%s' → %d new", location, keyword, new_found)
            time.sleep(2)  # polite rate-limit delay

    total = sum(len(v) for v in results.values())
    logger.info("JobSpy total: %d new jobs", total)
    return results


# ── Adzuna — official free API, 250 req/day ───────────────────────────────────

# Countries supported by Adzuna's API
_ADZUNA_COUNTRIES = [
    ("Europe",        "gb"),   # United Kingdom
    ("Europe",        "de"),   # Germany
    ("Europe",        "nl"),   # Netherlands
    ("Europe",        "se"),   # Sweden
    ("Europe",        "ch"),   # Switzerland
    ("Europe",        "es"),   # Spain
    ("Europe",        "ie"),   # Ireland
    ("Europe",        "at"),   # Austria
    ("Europe",        "it"),   # Italy
    ("Europe",        "pl"),   # Poland
    ("Asia",          "au"),   # Australia
    ("Asia",          "nz"),   # New Zealand
    ("Asia",          "sg"),   # Singapore
    ("USA_Canada",    "us"),   # United States
    ("USA_Canada",    "ca"),   # Canada
    ("South_America", "br"),   # Brazil
    ("South_America", "mx"),   # Mexico
]

_ADZUNA_BASE = "https://api.adzuna.com/v1/api/jobs/{country}/search/1"


def _normalize_adzuna(item: dict, region: str) -> Optional[dict]:
    job_id   = str(item.get("id") or "")
    title    = item.get("title") or ""
    company  = (item.get("company") or {}).get("display_name") or ""
    location = (item.get("location") or {}).get("display_name") or ""
    url      = item.get("redirect_url") or ""
    desc     = item.get("description") or ""
    posted   = item.get("created") or ""

    if not (title and url):
        return None
    if _is_excluded(location) or _is_blacklisted(company):
        return None

    return {
        "job_id":      f"adzuna_{job_id}",
        "source":      "Adzuna",
        "title":       title,
        "company":     company or "Unknown",
        "location":    location,
        "region":      region,
        "url":         url,
        "description": desc,
        "posted_at":   _parse_date(posted),
        "posted_raw":  str(posted),
        "found_at":    datetime.now(tz=timezone.utc),
    }


def scrape_adzuna(seen_ids: Set[str]) -> Dict[str, List[dict]]:
    """Scrape Adzuna official job board API — 250 free requests/day."""
    if not (ADZUNA_APP_ID and ADZUNA_APP_KEY):
        logger.info("  Adzuna skipped — ADZUNA_APP_ID / ADZUNA_APP_KEY not set.")
        return {r: [] for r in REGIONS}

    results: Dict[str, List[dict]] = {r: [] for r in REGIONS}
    seen_in_run: Set[str] = set()
    keywords = list(SEARCH_KEYWORDS[:3])

    for region_name, country in _ADZUNA_COUNTRIES:
        for keyword in keywords:
            try:
                r = _http.get(
                    _ADZUNA_BASE.format(country=country),
                    params={
                        "app_id":           ADZUNA_APP_ID,
                        "app_key":          ADZUNA_APP_KEY,
                        "what":             keyword,
                        "results_per_page": 50,
                        "max_days_old":     3,
                        "content-type":     "application/json",
                    },
                    headers={"Accept": "application/json"},
                    timeout=20,
                )
                r.raise_for_status()
                items = r.json().get("results", [])
            except Exception as exc:
                logger.warning("  Adzuna [%s] '%s' failed: %s", country.upper(), keyword, exc)
                continue

            new_found = 0
            for item in items:
                norm = _normalize_adzuna(item, region_name)
                if not norm:
                    continue
                jid = norm["job_id"]
                if jid in seen_ids or jid in seen_in_run:
                    continue
                seen_in_run.add(jid)
                results[region_name].append(norm)
                new_found += 1

            logger.info("  Adzuna [%s] '%s' → %d new", country.upper(), keyword, new_found)
            time.sleep(0.5)

    total = sum(len(v) for v in results.values())
    logger.info("Adzuna total: %d new jobs", total)
    return results


# ── JSearch (RapidAPI) — global aggregator, 500 free req/month ───────────────

# Priority query list — only JSEARCH_MAX_CALLS_PER_RUN are used per run
# Default budget: 8/run × 60 runs/month = 480 req/month (within 500 free tier)
_JSEARCH_QUERIES = [
    ("data science internship",      "London"),
    ("machine learning internship",  "Singapore"),
    ("AI internship",                "New York"),
    ("data science internship",      "Berlin"),
    ("machine learning internship",  "San Francisco"),
    ("AI internship",                "Toronto"),
    ("data science internship",      "Sydney"),
    ("machine learning internship",  "Dubai"),
    ("AI internship",                "Tokyo"),
    ("data science internship",      "Amsterdam"),
]

_JSEARCH_URL = "https://jsearch.p.rapidapi.com/search"


def _normalize_jsearch(item: dict) -> Optional[dict]:
    job_id   = str(item.get("job_id") or "")
    title    = item.get("job_title") or ""
    company  = item.get("employer_name") or ""
    city     = item.get("job_city") or ""
    country  = item.get("job_country") or ""
    location = f"{city}, {country}".strip(", ")
    url      = item.get("job_apply_link") or item.get("job_google_link") or ""
    desc     = item.get("job_description") or ""
    posted   = item.get("job_posted_at_datetime_utc") or ""

    if not (title and company and url):
        return None
    if _is_excluded(location) or _is_blacklisted(company):
        return None

    return {
        "job_id":      f"jsearch_{hashlib.md5(job_id.encode()).hexdigest()[:16]}",
        "source":      "JSearch",
        "title":       title,
        "company":     company,
        "location":    location,
        "region":      _infer_region(location),
        "url":         url,
        "description": desc[:3000],
        "posted_at":   _parse_date(posted),
        "posted_raw":  str(posted),
        "found_at":    datetime.now(tz=timezone.utc),
    }


def scrape_jsearch(seen_ids: Set[str]) -> Dict[str, List[dict]]:
    """Scrape via JSearch on RapidAPI — 500 free requests/month."""
    if not JSEARCH_API_KEY:
        logger.info("  JSearch skipped — JSEARCH_API_KEY not set.")
        return {r: [] for r in REGIONS}

    results: Dict[str, List[dict]] = {r: [] for r in REGIONS}
    seen_in_run: Set[str] = set()
    queries = list(_JSEARCH_QUERIES)
    random.shuffle(queries)
    queries = queries[:JSEARCH_MAX_CALLS_PER_RUN]

    for keyword, location in queries:
        try:
            r = _http.get(
                _JSEARCH_URL,
                headers={
                    "X-RapidAPI-Key":  JSEARCH_API_KEY,
                    "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
                },
                params={
                    "query":       f"{keyword} in {location}",
                    "num_pages":   "1",
                    "date_posted": "3days",
                },
                timeout=20,
            )
            r.raise_for_status()
            items = r.json().get("data", [])
        except Exception as exc:
            logger.warning("  JSearch ['%s' in %s] failed: %s", keyword, location, exc)
            continue

        new_found = 0
        for item in items:
            norm = _normalize_jsearch(item)
            if not norm:
                continue
            jid = norm["job_id"]
            if jid in seen_ids or jid in seen_in_run:
                continue
            seen_in_run.add(jid)
            results[norm["region"]].append(norm)
            new_found += 1

        logger.info("  JSearch ['%s' in %s] → %d new", keyword, location, new_found)
        time.sleep(1)

    total = sum(len(v) for v in results.values())
    logger.info("JSearch total: %d new jobs", total)
    return results


# ── SerpAPI — Google Jobs, 100 free searches/month ────────────────────────────

# Priority order — only SERPAPI_MAX_CALLS_PER_RUN are used per run
# Default budget: 2/run × 60 runs/month = 120 req/month (just over 100 free tier)
# Lower SERPAPI_MAX_CALLS_PER_RUN to 1 if you want to stay under 100/month.
_SERPAPI_QUERIES = [
    ("data science intern",       "London, United Kingdom"),
    ("machine learning intern",   "Singapore"),
    ("AI intern",                 "New York, United States"),
    ("data science intern",       "Berlin, Germany"),
    ("machine learning intern",   "San Francisco, California"),
]

_SERPAPI_URL = "https://serpapi.com/search"


def _normalize_serpapi_job(item: dict) -> Optional[dict]:
    job_id   = str(item.get("job_id") or "")
    title    = item.get("title") or ""
    company  = item.get("company_name") or ""
    location = item.get("location") or ""
    desc     = item.get("description") or ""
    posted   = (item.get("detected_extensions") or {}).get("posted_at") or ""
    apply    = item.get("apply_options") or []
    url      = apply[0].get("link", "") if apply else item.get("share_link") or ""

    if not (title and company):
        return None
    if _is_excluded(location) or _is_blacklisted(company):
        return None

    if not job_id:
        job_id = hashlib.md5(f"{title}::{company}::{location}".encode()).hexdigest()[:16]
    if not url:
        url = (f"https://www.google.com/search?"
               f"q={urllib.parse.quote(title + ' ' + company)}&ibp=htl;jobs")

    return {
        "job_id":      f"google_{hashlib.md5(job_id.encode()).hexdigest()[:16]}",
        "source":      "Google Jobs",
        "title":       title,
        "company":     company,
        "location":    location,
        "region":      _infer_region(location),
        "url":         url,
        "description": desc[:3000],
        "posted_at":   _parse_date(posted),
        "posted_raw":  str(posted),
        "found_at":    datetime.now(tz=timezone.utc),
    }


def scrape_serpapi(seen_ids: Set[str]) -> Dict[str, List[dict]]:
    """Scrape Google Jobs via SerpAPI — 100 free searches/month."""
    if not SERPAPI_KEY:
        logger.info("  SerpAPI skipped — SERPAPI_KEY not set.")
        return {r: [] for r in REGIONS}

    results: Dict[str, List[dict]] = {r: [] for r in REGIONS}
    seen_in_run: Set[str] = set()
    queries = list(_SERPAPI_QUERIES[:SERPAPI_MAX_CALLS_PER_RUN])

    for keyword, location in queries:
        try:
            r = _http.get(
                _SERPAPI_URL,
                params={
                    "engine":   "google_jobs",
                    "q":        keyword,
                    "location": location,
                    "api_key":  SERPAPI_KEY,
                    "hl":       "en",
                    "chips":    "date_posted:3days",
                },
                timeout=20,
            )
            r.raise_for_status()
            items = r.json().get("jobs_results", [])
        except Exception as exc:
            logger.warning("  SerpAPI ['%s' in %s] failed: %s", keyword, location, exc)
            continue

        new_found = 0
        for item in items:
            norm = _normalize_serpapi_job(item)
            if not norm:
                continue
            jid = norm["job_id"]
            if jid in seen_ids or jid in seen_in_run:
                continue
            seen_in_run.add(jid)
            results[norm["region"]].append(norm)
            new_found += 1

        logger.info("  SerpAPI ['%s' in %s] → %d new", keyword, location, new_found)
        time.sleep(1)

    total = sum(len(v) for v in results.values())
    logger.info("SerpAPI total: %d new jobs", total)
    return results


# ── RemoteOK — free public JSON API ──────────────────────────────────────────

_REMOTEOK_TAGS = ["machine-learning", "data-science", "ai", "deep-learning", "python"]
_REMOTEOK_HINTS = [
    "intern", "internship", "trainee", "apprentice",
    "junior", "entry", "student", "graduate", "new grad",
]


def _normalize_remoteok(item: dict) -> Optional[dict]:
    if not isinstance(item, dict):
        return None
    job_id  = str(item.get("id") or item.get("slug") or "")
    title   = item.get("position") or ""
    company = item.get("company") or ""
    url     = item.get("url") or (f"https://remoteok.com/l/{job_id}" if job_id else "")
    desc    = item.get("description") or str(item.get("tags_label") or "")
    posted  = item.get("date") or item.get("epoch") or ""

    if not (title and company and job_id):
        return None
    if _is_blacklisted(company):
        return None

    text_lower = (title + " " + desc).lower()
    is_intern = any(h in text_lower for h in _REMOTEOK_HINTS)
    is_ml = any(kw in text_lower for kw in [
        "machine learning", "deep learning", "data science",
        "ai engineer", "ml engineer", "data analyst", "nlp", "computer vision",
    ])
    if not (is_intern or is_ml):
        return None

    return {
        "job_id":      f"remoteok_{job_id}",
        "source":      "RemoteOK",
        "title":       title,
        "company":     company,
        "location":    "Remote",
        "region":      "USA_Canada",
        "url":         url,
        "description": desc[:2000],
        "posted_at":   _parse_date(posted),
        "posted_raw":  str(posted),
        "found_at":    datetime.now(tz=timezone.utc),
    }


def scrape_remoteok(seen_ids: Set[str]) -> Dict[str, List[dict]]:
    """Scrape RemoteOK via free public JSON API — no key required."""
    results: Dict[str, List[dict]] = {r: [] for r in REGIONS}
    seen_in_run: Set[str] = set()

    for tag in _REMOTEOK_TAGS:
        try:
            r = _http.get(
                f"https://remoteok.com/api?tags={tag}",
                headers={"User-Agent": "career-ops/1.0 (internship-scraper)"},
                timeout=15,
            )
            r.raise_for_status()
            items = r.json()
        except Exception as exc:
            logger.warning("  RemoteOK [%s] failed: %s", tag, exc)
            continue

        new_found = 0
        for item in items:
            norm = _normalize_remoteok(item)
            if norm and norm["job_id"] not in seen_ids and norm["job_id"] not in seen_in_run:
                results[norm["region"]].append(norm)
                seen_in_run.add(norm["job_id"])
                new_found += 1

        logger.info("  RemoteOK [%s] → %d new", tag, new_found)

    return results


# ── Merge · Deduplicate · Filter ──────────────────────────────────────────────

def _filter_stale_jobs(jobs: List[dict]) -> List[dict]:
    if not MAX_JOB_AGE_DAYS:
        return jobs
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=MAX_JOB_AGE_DAYS)
    fresh, stale = [], []
    for j in jobs:
        if j.get("posted_at") and j["posted_at"] < cutoff:
            stale.append(j)
        else:
            fresh.append(j)
    if stale:
        logger.info("Age filter: dropped %d jobs older than %d days", len(stale), MAX_JOB_AGE_DAYS)
    return fresh


def _dedupe_region(jobs: List[dict]) -> List[dict]:
    """Deduplicate by job_id AND by (title, company) to catch same job from multiple sources."""
    seen_jids: Set[str] = set()
    seen_content: Set[tuple] = set()
    out = []
    for j in jobs:
        jid = j["job_id"]
        key = (j.get("title", "").lower().strip(), j.get("company", "").lower().strip())
        if jid not in seen_jids and key not in seen_content:
            seen_jids.add(jid)
            seen_content.add(key)
            out.append(j)
    return out


def _same_hour_priority(jobs: List[dict]) -> List[dict]:
    now_hour = datetime.now(tz=timezone.utc).hour
    same = [j for j in jobs if j.get("posted_at") and j["posted_at"].hour == now_hour]
    rest = [j for j in jobs if j not in same]
    return same + rest


def _merge_sources(*source_dicts) -> Dict[str, List[dict]]:
    """Combine results from multiple scrapers into one region-keyed dict."""
    merged: Dict[str, List[dict]] = {r: [] for r in REGIONS}
    for src in source_dicts:
        for region, jobs in src.items():
            merged[region].extend(jobs)
    return {region: _dedupe_region(jobs) for region, jobs in merged.items()}


# ── Main entry point ──────────────────────────────────────────────────────────

def scrape_all(
    seen_ids: Set[str],
    seen_fingerprints: Optional[Set[str]] = None,
    extra_keywords: Optional[List[str]] = None,
) -> List[dict]:
    """
    Full multi-source scrape. Returns deduplicated, quota-balanced, sorted jobs.

    Sources: JobSpy · Adzuna · JSearch · SerpAPI · RemoteOK · free scrapers
    seen_fingerprints : title+company hashes seen in previous runs
    extra_keywords    : additional keywords from /search Telegram command
    """
    if seen_fingerprints is None:
        seen_fingerprints = set()

    if extra_keywords:
        import config as _cfg
        original_keywords = list(_cfg.SEARCH_KEYWORDS)
        _cfg.SEARCH_KEYWORDS = list(_cfg.SEARCH_KEYWORDS) + [
            kw for kw in extra_keywords if kw not in _cfg.SEARCH_KEYWORDS
        ]
        logger.info("Extra search keywords: %s", extra_keywords)
    else:
        original_keywords = None

    logger.info("── Scraping JobSpy (LinkedIn / Indeed / Glassdoor) ──")
    jobspy_results   = scrape_jobspy(seen_ids)

    logger.info("── Scraping Adzuna (official API, 15 countries) ─────")
    adzuna_results   = scrape_adzuna(seen_ids)

    logger.info("── Scraping JSearch (RapidAPI aggregator) ───────────")
    jsearch_results  = scrape_jsearch(seen_ids)

    logger.info("── Scraping SerpAPI (Google Jobs) ───────────────────")
    serpapi_results  = scrape_serpapi(seen_ids)

    logger.info("── Scraping RemoteOK (startup / ML remote jobs) ─────")
    remoteok_results = scrape_remoteok(seen_ids)

    logger.info("── Scraping free sources (Arbeitnow / Jobteaser …) ──")
    try:
        from scraper_free import scrape_free_sources
        free_results = scrape_free_sources(seen_ids)
    except Exception as exc:
        logger.warning("Free scrapers failed: %s", exc)
        free_results = {r: [] for r in REGIONS}

    combined = _merge_sources(
        jobspy_results, adzuna_results, jsearch_results,
        serpapi_results, remoteok_results, free_results,
    )

    # Belt-and-suspenders: drop anything already in seen_ids
    for region in combined:
        combined[region] = [j for j in combined[region] if j["job_id"] not in seen_ids]

    # Drop jobs whose title+company fingerprint was seen in a previous run
    if seen_fingerprints:
        before = sum(len(v) for v in combined.values())
        for region in combined:
            combined[region] = [
                j for j in combined[region]
                if _make_fingerprint(j.get("title", ""), j.get("company", ""))
                not in seen_fingerprints
            ]
        after = sum(len(v) for v in combined.values())
        if before != after:
            logger.info("Fingerprint filter: dropped %d cross-run duplicate jobs", before - after)

    # Drop stale listings (posted_at older than MAX_JOB_AGE_DAYS)
    all_jobs_flat = _filter_stale_jobs([j for jobs in combined.values() for j in jobs])
    combined = {r: [] for r in REGIONS}
    for j in all_jobs_flat:
        combined[j["region"]].append(j)

    total = sum(len(v) for v in combined.values())
    if total == 0:
        logger.info("No new jobs found across all sources.")
        return []

    # Enforce regional quota (weights from config)
    quotas = {region: int(total * cfg["weight"]) for region, cfg in REGIONS.items()}
    selected: List[dict] = []
    for region_name, jobs in combined.items():
        cap = quotas[region_name]
        selected.extend(jobs[:cap] if len(jobs) >= cap else jobs)

    # Sort: same-hour first, then most recent
    selected = _same_hour_priority(selected)
    selected.sort(
        key=lambda j: j["posted_at"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    # Log source breakdown
    source_counts: Dict[str, int] = {}
    for j in selected:
        source_counts[j["source"]] = source_counts.get(j["source"], 0) + 1

    logger.info(
        "Total new jobs: %d | EU=%d AS=%d US/CA=%d LATAM=%d ME=%d | by source: %s",
        len(selected),
        sum(1 for j in selected if j["region"] == "Europe"),
        sum(1 for j in selected if j["region"] == "Asia"),
        sum(1 for j in selected if j["region"] == "USA_Canada"),
        sum(1 for j in selected if j["region"] == "South_America"),
        sum(1 for j in selected if j["region"] == "Middle_East"),
        source_counts,
    )

    if original_keywords is not None:
        import config as _cfg
        _cfg.SEARCH_KEYWORDS = original_keywords

    return selected


# ── Persistence helpers ───────────────────────────────────────────────────────

def load_seen_ids(path) -> Set[str]:
    try:
        with open(str(path)) as f:
            return set(line.strip() for line in f if line.strip())
    except FileNotFoundError:
        return set()


def save_seen_ids(path, ids: Set[str]) -> None:
    with open(str(path), "w") as f:
        f.write("\n".join(sorted(ids)))


def load_seen_fingerprints(path) -> Set[str]:
    try:
        with open(str(path)) as f:
            return set(line.strip() for line in f if line.strip())
    except FileNotFoundError:
        return set()


def save_seen_fingerprints(path, fps: Set[str]) -> None:
    with open(str(path), "w") as f:
        f.write("\n".join(sorted(fps)))
