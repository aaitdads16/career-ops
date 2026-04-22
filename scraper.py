"""
Multi-source internship scraper.
Sources: Indeed · LinkedIn · Glassdoor · Google Jobs · Wellfound (startup jobs)
All routed through Apify actors, deduplicated, and quota-balanced by region.
"""

import logging
import random
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set

import requests as _http
from apify_client import ApifyClient

from config import (
    ACTOR_GLASSDOOR,
    ACTOR_GOOGLE_JOBS,
    ACTOR_INDEED,
    ACTOR_LINKEDIN,
    ACTOR_WELLFOUND,
    APIFY_API_TOKEN,
    COMPANY_BLACKLIST,
    DATE_POSTED,
    GLASSDOOR_DAYS_OLD,
    LINKEDIN_COUNT,
    LINKEDIN_HOURS,
    LINKEDIN_REGIONS,
    MAX_JOB_AGE_DAYS,
    REGIONS,
    RESULTS_PER_SEARCH,
    SEARCH_KEYWORDS,
    WELLFOUND_MAX,
    linkedin_url,
)

logger = logging.getLogger(__name__)

_EXCLUDED_LOCATIONS = [
    "france", "paris", "lyon", "marseille", "toulouse", "nice",
    "morocco", "maroc", "casablanca", "rabat",
    "egypt", "nigeria", "kenya", "south africa", "algerie", "algeria",
    "tunisia", "ghana", "senegal", "ethiopia", "tanzania",
    "cameroon", "ivory coast", "côte d'ivoire", "abidjan",
]


# ── Shared helpers ────────────────────────────────────────────────────────────

def _is_excluded(location: str) -> bool:
    loc = (location or "").lower()
    return any(ex in loc for ex in _EXCLUDED_LOCATIONS)


def _is_blacklisted(company: str) -> bool:
    """Return True if company matches any entry in COMPANY_BLACKLIST."""
    name = (company or "").lower()
    return any(bl in name for bl in COMPANY_BLACKLIST)


def _parse_date(raw) -> Optional[datetime]:
    if not raw:
        return None
    try:
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(raw / 1000, tz=timezone.utc)
        from dateutil import parser as dp
        dt = dp.parse(str(raw))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _run_actor(client: ApifyClient, actor: str, run_input: dict, timeout: int = 120) -> List[dict]:
    try:
        run = client.actor(actor).call(run_input=run_input, timeout_secs=timeout)
        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
        return items
    except Exception as exc:
        logger.warning("Apify [%s] failed: %s", actor, exc)
        return []


# ── Indeed ────────────────────────────────────────────────────────────────────

def _normalize_indeed(item: dict, region: str, source: str = "Indeed") -> Optional[dict]:
    job_id  = str(item.get("id") or item.get("jobKey") or item.get("jobId") or "")
    title   = item.get("title") or item.get("jobTitle") or ""
    company = item.get("company") or item.get("companyName") or ""
    location= item.get("location") or item.get("jobLocation") or ""
    url     = item.get("url") or item.get("jobUrl") or item.get("applyUrl") or ""
    desc    = item.get("description") or item.get("jobDescription") or ""
    posted  = item.get("date") or item.get("postedAt") or item.get("datePosted") or ""

    if not (title and company and url) or _is_excluded(location) or _is_blacklisted(company):
        return None

    if not job_id:
        job_id = url

    return {
        "job_id":      f"indeed_{job_id}",
        "source":      source,
        "title":       title,
        "company":     company,
        "location":    location,
        "region":      region,
        "url":         url,
        "description": desc,
        "posted_at":   _parse_date(posted),
        "posted_raw":  str(posted),
        "found_at":    datetime.now(tz=timezone.utc),
    }


def scrape_indeed(client: ApifyClient, seen_ids: Set[str]) -> Dict[str, List[dict]]:
    """Scrape Indeed across all regions. Returns dict keyed by region name."""
    results: Dict[str, List[dict]] = {r: [] for r in REGIONS}
    for region_name, region_cfg in REGIONS.items():
        searches = list(region_cfg["searches"])
        random.shuffle(searches)
        for search in searches:
            for keyword in SEARCH_KEYWORDS:
                items = _run_actor(client, ACTOR_INDEED, {
                    "country":    search["country"],
                    "title":      keyword,
                    "location":   search["location"],
                    "limit":      RESULTS_PER_SEARCH,
                    "datePosted": DATE_POSTED,
                })
                logger.info("  Indeed [%s/%s] '%s' → %d",
                            search["country"].upper(), search["location"], keyword, len(items))
                for item in items:
                    norm = _normalize_indeed(item, region_name)
                    if norm and norm["job_id"] not in seen_ids:
                        results[region_name].append(norm)
    return results


# ── LinkedIn ──────────────────────────────────────────────────────────────────

def _normalize_linkedin(item: dict, region: str) -> Optional[dict]:
    job_id  = str(item.get("id") or item.get("jobId") or "")
    title   = item.get("title") or item.get("jobTitle") or ""
    company = (item.get("company") or {}).get("name") or item.get("companyName") or ""
    location= item.get("location") or ""
    url     = item.get("link") or item.get("jobUrl") or item.get("url") or ""
    desc    = item.get("descriptionHtml") or item.get("description") or ""
    posted  = item.get("postedAt") or item.get("datePosted") or ""

    if not (title and company and url) or _is_excluded(location) or _is_blacklisted(company):
        return None

    if not job_id:
        job_id = url

    return {
        "job_id":      f"linkedin_{job_id}",
        "source":      "LinkedIn",
        "title":       title,
        "company":     company,
        "location":    location,
        "region":      region,
        "url":         url,
        "description": desc,
        "posted_at":   _parse_date(posted),
        "posted_raw":  str(posted),
        "found_at":    datetime.now(tz=timezone.utc),
    }


# ── LinkedIn scraping strategy ────────────────────────────────────────────────
#
# TIER 1 — splitByLocation (key countries)
#   The actor has a built-in city-splitting feature that automatically generates
#   one search per city within a country. This gives comprehensive national
#   coverage in a single call and bypasses LinkedIn's 1000-result search cap.
#   We use this for the highest-volume markets.
#
# TIER 2 — Manual city URLs (remaining markets)
#   For markets not covered by Tier 1, use explicit city URLs.
#
# count=100 (actor default) — we were using 10, which was 10x under-requesting.

_LINKEDIN_SPLIT_COUNTRIES = [
    # (region_name, country_code)
    ("Europe",     "GB"),   # United Kingdom
    ("Europe",     "DE"),   # Germany
    ("Europe",     "NL"),   # Netherlands
    ("Europe",     "CH"),   # Switzerland
    ("Europe",     "SE"),   # Sweden
    ("Europe",     "PL"),   # Poland
    ("Europe",     "IT"),   # Italy
    ("Europe",     "ES"),   # Spain
    ("Europe",     "BE"),   # Belgium
    ("Asia",       "SG"),   # Singapore
    ("Asia",       "IN"),   # India
    ("Asia",       "JP"),   # Japan
    ("Asia",       "AU"),   # Australia
    ("Asia",       "IL"),   # Israel
    ("USA_Canada", "US"),   # United States
    ("USA_Canada", "CA"),   # Canada
]

_LINKEDIN_MANUAL_CITIES = {
    "Europe":        [("Dublin","IE"),("Barcelona","ES"),("Copenhagen","DK"),
                      ("Oslo","NO"),("Helsinki","FI"),("Vienna","AT"),("Milan","IT"),
                      ("Lisbon","PT"),("Prague","CZ"),("Warsaw","PL"),("Athens","GR"),
                      ("Budapest","HU"),("Bucharest","RO"),("Tallinn","EE"),
                      ("Zurich","CH"),("Brussels","BE"),("Luxembourg","LU")],
    "Asia":          [("Seoul","KR"),("Hong Kong","HK"),("Kuala Lumpur","MY"),
                      ("Bangkok","TH"),("Ho Chi Minh City","VN"),("Manila","PH"),
                      ("Sydney","AU"),("Melbourne","AU"),("Auckland","NZ"),
                      ("Tel Aviv","IL"),("Shanghai","CN"),("Beijing","CN")],
    "South_America": [("São Paulo","BR"),("Buenos Aires","AR"),("Mexico City","MX"),
                      ("Bogotá","CO"),("Santiago","CL"),("Lima","PE")],
    "Middle_East":   [("Dubai","AE"),("Abu Dhabi","AE"),("Riyadh","SA"),
                      ("Doha","QA"),("Tel Aviv","IL")],
    "USA_Canada":    [("Austin","US"),("Chicago","US"),("Los Angeles","US"),
                      ("Washington","US"),("Atlanta","US"),("Vancouver","CA"),
                      ("Calgary","CA")],
}


def _linkedin_base_url(keyword: str) -> str:
    """Base LinkedIn search URL without location (used with splitByLocation)."""
    params = {
        "keywords": keyword,
        "f_TPR":    f"r{LINKEDIN_HOURS}",
        "sortBy":   "DD",
        "position": "1",
        "pageNum":  "0",
    }
    return "https://www.linkedin.com/jobs/search/?" + urllib.parse.urlencode(params)


def scrape_linkedin(client: ApifyClient, seen_ids: Set[str]) -> Dict[str, List[dict]]:
    """
    Two-tier LinkedIn scraping strategy.

    Tier 1 (splitByLocation): 10 key countries × 4 keywords = 40 actor calls.
      Each call triggers the actor's built-in city-splitting — it fans out into
      every major city in that country automatically, giving full national coverage
      and bypassing LinkedIn's 1000-job-per-URL cap.

    Tier 2 (manual city URLs): remaining cities not in Tier 1 countries.

    count=100 per call (was 10 — a 10× increase).
    f_JT=I removed — was blocking 60-70%% of internships not tagged correctly.
    """
    results: Dict[str, List[dict]] = {r: [] for r in REGIONS}

    # ── Tier 1: country-level split ──────────────────────────────────────────
    logger.info("  LinkedIn Tier 1 (splitByLocation): %d country×keyword calls",
                len(_LINKEDIN_SPLIT_COUNTRIES) * len(SEARCH_KEYWORDS))

    for region_name, country_code in _LINKEDIN_SPLIT_COUNTRIES:
        for keyword in SEARCH_KEYWORDS:
            base_url = _linkedin_base_url(keyword)
            items = _run_actor(client, ACTOR_LINKEDIN, {
                "urls":            [base_url],
                "scrapeCompany":   False,
                "count":           100,
                "splitByLocation": True,
                "splitCountry":    country_code,
            }, timeout=300)  # longer timeout — city-splitting generates more work

            new_found = 0
            for item in items:
                norm = _normalize_linkedin(item, region_name)
                if norm and norm["job_id"] not in seen_ids:
                    results[region_name].append(norm)
                    new_found += 1

            logger.info("  LinkedIn Tier1 [%s/%s] '%s' → %d raw / %d new",
                        region_name, country_code, keyword, len(items), new_found)

    # ── Tier 2: manual city URLs ─────────────────────────────────────────────
    for region_name, cities in _LINKEDIN_MANUAL_CITIES.items():
        if not cities:
            continue
        shuffled = list(cities)
        random.shuffle(shuffled)
        for city, _ in shuffled:
            for keyword in SEARCH_KEYWORDS:
                url = linkedin_url(keyword, city)
                items = _run_actor(client, ACTOR_LINKEDIN, {
                    "urls":          [url],
                    "scrapeCompany": False,
                    "count":         LINKEDIN_COUNT,
                }, timeout=180)

                new_found = 0
                for item in items:
                    norm = _normalize_linkedin(item, region_name)
                    if norm and norm["job_id"] not in seen_ids:
                        results[region_name].append(norm)
                        new_found += 1

                logger.info("  LinkedIn Tier2 [%s] '%s' → %d raw / %d new",
                            city, keyword, len(items), new_found)

    return results


# ── Glassdoor ─────────────────────────────────────────────────────────────────

def _normalize_glassdoor(item: dict, region: str) -> Optional[dict]:
    job_id  = str(item.get("id") or item.get("jobId") or item.get("jobListingId") or "")
    title   = item.get("jobTitle") or item.get("title") or ""
    company = item.get("employerName") or item.get("company") or ""
    location= item.get("location") or item.get("jobLocation") or ""
    url     = item.get("jobUrl") or item.get("url") or ""
    desc    = item.get("jobDescription") or item.get("description") or ""
    posted  = item.get("age") or item.get("postedAt") or item.get("datePosted") or ""

    if not (title and company and url) or _is_excluded(location) or _is_blacklisted(company):
        return None

    if not job_id:
        job_id = url

    return {
        "job_id":      f"glassdoor_{job_id}",
        "source":      "Glassdoor",
        "title":       title,
        "company":     company,
        "location":    location,
        "region":      region,
        "url":         url,
        "description": desc,
        "posted_at":   _parse_date(posted),
        "posted_raw":  str(posted),
        "found_at":    datetime.now(tz=timezone.utc),
    }


def scrape_glassdoor(client: ApifyClient, seen_ids: Set[str]) -> Dict[str, List[dict]]:
    """Scrape Glassdoor for each region."""
    results: Dict[str, List[dict]] = {r: [] for r in REGIONS}
    for region_name, region_cfg in REGIONS.items():
        searches = list(region_cfg["searches"])
        random.shuffle(searches)
        for search in searches[:4]:   # cap to 4 cities per region to control cost
            for keyword in SEARCH_KEYWORDS[:2]:   # top 2 keywords only on Glassdoor
                items = _run_actor(client, ACTOR_GLASSDOOR, {
                    "keywords": keyword,
                    "location": search["location"],
                    "daysOld":  GLASSDOOR_DAYS_OLD,
                    "limit":    RESULTS_PER_SEARCH,
                })
                logger.info("  Glassdoor [%s] '%s' → %d",
                            search["location"], keyword, len(items))
                for item in items:
                    norm = _normalize_glassdoor(item, region_name)
                    if norm and norm["job_id"] not in seen_ids:
                        results[region_name].append(norm)
    return results


# ── Wellfound (startup jobs — free actor) ────────────────────────────────────

# Wellfound actor removed — replaced by RemoteOK free API (see scrape_remoteok below)

_REMOTEOK_TAGS = [
    "machine-learning",
    "data-science",
    "ai",
    "deep-learning",
    "python",
]

_REMOTEOK_INTERNSHIP_HINTS = [
    "intern", "internship", "trainee", "apprentice", "junior", "entry",
    "student", "graduate", "new grad",
]


def _normalize_remoteok(item: dict) -> Optional[dict]:
    """Normalize a RemoteOK API item to the standard job dict."""
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

    # Keep internship-relevant posts only
    text_lower = (title + " " + desc).lower()
    is_intern  = any(h in text_lower for h in _REMOTEOK_INTERNSHIP_HINTS)
    is_ml      = any(kw in text_lower for kw in [
        "machine learning", "deep learning", "data science", "ai engineer",
        "ml engineer", "data analyst", "nlp", "computer vision",
    ])
    if not (is_intern or is_ml):
        return None

    return {
        "job_id":      f"remoteok_{job_id}",
        "source":      "RemoteOK",
        "title":       title,
        "company":     company,
        "location":    "Remote",
        "region":      "USA_Canada",   # RemoteOK is predominantly US/global remote
        "url":         url,
        "description": desc[:2000],
        "posted_at":   _parse_date(posted),
        "posted_raw":  str(posted),
        "found_at":    datetime.now(tz=timezone.utc),
    }


def _infer_region(location: str) -> str:
    loc = location.lower()
    europe_hints = [
        "london","berlin","munich","amsterdam","stockholm","zurich","barcelona","madrid",
        "dublin","brussels","copenhagen","oslo","helsinki","milan","lisbon","warsaw",
        "prague","vienna","bucharest","budapest","rome","paris",
        "uk","de","nl","se","ch","es","ie","be","dk","no","fi","it","pt","pl","cz","at","ro","hu","eu","europe",
    ]
    asia_hints = [
        "singapore","tokyo","seoul","hong kong","bangalore","mumbai","kuala lumpur",
        "taipei","bangkok","jakarta","sg","jp","kr","hk","in","my","tw","th","id","asia",
    ]
    latam_hints = [
        "são paulo","sao paulo","rio de janeiro","buenos aires","bogotá","bogota",
        "santiago","mexico city","ciudad de mexico","br","ar","co","cl","mx",
        "brazil","argentina","colombia","chile","mexico","latin america","south america",
    ]
    me_hints = ["dubai","abu dhabi","riyadh","doha","ae","sa","qa","middle east","gulf"]

    for h in europe_hints:
        if h in loc: return "Europe"
    for h in asia_hints:
        if h in loc: return "Asia"
    for h in latam_hints:
        if h in loc: return "South_America"
    for h in me_hints:
        if h in loc: return "Middle_East"
    return "USA_Canada"


def scrape_remoteok(seen_ids: Set[str]) -> Dict[str, List[dict]]:
    """
    Scrape RemoteOK via their free public JSON API — no Apify actor, no key needed.
    Replaces broken Wellfound actor. Strong ML/AI startup coverage.
    """
    results: Dict[str, List[dict]] = {r: [] for r in REGIONS}
    seen_in_run: set = set()

    for tag in _REMOTEOK_TAGS:
        url = f"https://remoteok.com/api?tags={tag}"
        try:
            r = _http.get(
                url,
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


# ── Google Jobs ──────────────────────────────────────────────────────────────

# Top cities per region to drive Google Jobs searches (kept tight to control cost)
_GOOGLE_JOBS_CITIES = {
    "Europe":        ["London", "Berlin", "Amsterdam", "Zurich", "Stockholm",
                      "Dublin", "Barcelona", "Warsaw", "Vienna", "Milan",
                      "Lisbon", "Copenhagen", "Brussels"],
    "Asia":          ["Singapore", "Tokyo", "Bangalore", "Seoul", "Sydney",
                      "Tel Aviv", "Hong Kong", "Kuala Lumpur"],
    "USA_Canada":    ["New York", "San Francisco", "Boston", "Seattle",
                      "Austin", "Chicago", "Toronto", "Vancouver"],
    "South_America": ["São Paulo", "Buenos Aires", "Mexico City", "Bogotá"],
    "Middle_East":   ["Dubai", "Tel Aviv", "Riyadh"],
}


def _normalize_google_jobs(item: dict, region: str) -> Optional[dict]:
    # Google Jobs actor returns various field names depending on version
    job_id  = str(item.get("id") or item.get("jobId") or "")
    title   = item.get("title") or item.get("jobTitle") or ""
    company = item.get("company") or item.get("companyName") or item.get("employer") or ""
    location= item.get("location") or item.get("jobLocation") or ""
    url     = item.get("applyLink") or item.get("url") or item.get("jobUrl") or item.get("shareLink") or ""
    desc    = item.get("description") or item.get("jobDescription") or ""
    posted  = item.get("postedAt") or item.get("datePosted") or item.get("publishedAt") or ""

    if not (title and company) or _is_excluded(location) or _is_blacklisted(company):
        return None

    if not job_id:
        import hashlib
        job_id = hashlib.md5(f"{title}::{company}::{location}".encode()).hexdigest()[:16]
    if not url:
        url = f"https://www.google.com/search?q={urllib.parse.quote(title+' '+company)}&ibp=htl;jobs"

    return {
        "job_id":      f"google_{job_id}",
        "source":      "Google Jobs",
        "title":       title,
        "company":     company,
        "location":    location,
        "region":      region,
        "url":         url,
        "description": desc,
        "posted_at":   _parse_date(posted),
        "posted_raw":  str(posted),
        "found_at":    datetime.now(tz=timezone.utc),
    }


def scrape_google_jobs(client: ApifyClient, seen_ids: Set[str]) -> Dict[str, List[dict]]:
    """Scrape Google Jobs — broad aggregator covering company sites, boards, and more."""
    results: Dict[str, List[dict]] = {r: [] for r in REGIONS}

    for region_name, cities in _GOOGLE_JOBS_CITIES.items():
        for city in cities:
            for keyword in SEARCH_KEYWORDS[:2]:   # top 2 keywords per city
                query = f"{keyword} {city}"
                items = _run_actor(client, ACTOR_GOOGLE_JOBS, {
                    "queries":          [query],
                    "maxJobsPerQuery":  RESULTS_PER_SEARCH,
                    "languageCode":     "en",
                    "datePosted":       "3days",
                }, timeout=90)
                logger.info("  Google Jobs [%s] '%s' → %d", city, keyword, len(items))
                for item in items:
                    norm = _normalize_google_jobs(item, region_name)
                    if norm and norm["job_id"] not in seen_ids:
                        results[region_name].append(norm)

    return results


# ── Merge, deduplicate, and apply quota ───────────────────────────────────────

def _filter_stale_jobs(jobs: List[dict]) -> List[dict]:
    """
    Drop jobs whose posted_at is older than MAX_JOB_AGE_DAYS.
    Jobs with no posted_at date are kept (can't determine age).
    """
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
    seen_ids: Set[str] = set()
    seen_content: Set[tuple] = set()
    out = []
    for j in jobs:
        jid = j["job_id"]
        # Normalize title+company for content-based dedup
        key = (
            j.get("title", "").lower().strip(),
            j.get("company", "").lower().strip(),
        )
        if jid not in seen_ids and key not in seen_content:
            seen_ids.add(jid)
            seen_content.add(key)
            out.append(j)
    return out


def _same_hour_priority(jobs: List[dict]) -> List[dict]:
    now_hour = datetime.now(tz=timezone.utc).hour
    same = [j for j in jobs if j["posted_at"] and j["posted_at"].hour == now_hour]
    rest = [j for j in jobs if j not in same]
    return same + rest


def _merge_sources(*source_dicts) -> Dict[str, List[dict]]:
    """Combine results from multiple scrapers into one dict per region."""
    merged: Dict[str, List[dict]] = {r: [] for r in REGIONS}
    for src in source_dicts:
        for region, jobs in src.items():
            merged[region].extend(jobs)
    # Deduplicate per region
    return {region: _dedupe_region(jobs) for region, jobs in merged.items()}


def scrape_all(
    seen_ids: Set[str],
    seen_fingerprints: Optional[Set[str]] = None,
    extra_keywords: Optional[List[str]] = None,
) -> List[dict]:
    """
    Full multi-source scrape. Returns deduplicated, quota-balanced, sorted jobs.
    Sources: Indeed + LinkedIn + Glassdoor + Google Jobs + RemoteOK (free).

    seen_fingerprints: set of title+company hashes from previous runs.
    extra_keywords:    additional keywords from /search Telegram command.
    """
    if seen_fingerprints is None:
        seen_fingerprints = set()
    if not APIFY_API_TOKEN:
        raise ValueError("APIFY_API_TOKEN is not set. Add it to your .env file.")

    # Temporarily extend SEARCH_KEYWORDS for this run
    if extra_keywords:
        import config as _cfg
        original_keywords = list(_cfg.SEARCH_KEYWORDS)
        _cfg.SEARCH_KEYWORDS = list(_cfg.SEARCH_KEYWORDS) + [
            kw for kw in extra_keywords if kw not in _cfg.SEARCH_KEYWORDS
        ]
        logger.info("Extra search keywords: %s", extra_keywords)
    else:
        original_keywords = None

    client = ApifyClient(APIFY_API_TOKEN)

    logger.info("── Scraping Indeed ──────────────────────────────────")
    indeed_results    = scrape_indeed(client, seen_ids)

    logger.info("── Scraping LinkedIn ────────────────────────────────")
    linkedin_results  = scrape_linkedin(client, seen_ids)

    logger.info("── Scraping Glassdoor ───────────────────────────────")
    glassdoor_results = scrape_glassdoor(client, seen_ids)

    logger.info("── Scraping Google Jobs ─────────────────────────────")
    google_results    = scrape_google_jobs(client, seen_ids)

    logger.info("── Scraping RemoteOK (startup/ML jobs) ──────────────")
    wellfound_results = scrape_remoteok(seen_ids)   # variable name kept for merge compat

    # Free sources (no API key required)
    try:
        from scraper_free import scrape_free_sources
        free_results = scrape_free_sources(seen_ids)
    except Exception as exc:
        logger.warning("Free scrapers failed: %s", exc)
        free_results = {r: [] for r in REGIONS}

    # Merge all sources
    combined = _merge_sources(
        indeed_results, linkedin_results,
        glassdoor_results, google_results,
        wellfound_results, free_results,
    )

    # Remove jobs already in seen_ids (belt-and-suspenders)
    for region in combined:
        combined[region] = [j for j in combined[region] if j["job_id"] not in seen_ids]

    # Remove jobs whose title+company fingerprint was seen in a previous run
    # (catches the same real job re-scraped under a different job_id)
    if seen_fingerprints:
        before = sum(len(v) for v in combined.values())
        for region in combined:
            combined[region] = [
                j for j in combined[region]
                if _make_fingerprint(j.get("title", ""), j.get("company", "")) not in seen_fingerprints
            ]
        after = sum(len(v) for v in combined.values())
        if before != after:
            logger.info("Fingerprint filter: dropped %d cross-run duplicate jobs", before - after)

    # Drop stale listings (posted_at older than MAX_JOB_AGE_DAYS)
    all_jobs_flat = [j for jobs in combined.values() for j in jobs]
    all_jobs_flat = _filter_stale_jobs(all_jobs_flat)
    # Re-bucket by region after age filter
    combined = {r: [] for r in REGIONS}
    for j in all_jobs_flat:
        combined[j["region"]].append(j)

    total = sum(len(v) for v in combined.values())
    if total == 0:
        logger.info("No new jobs found across all sources.")
        return []

    # Enforce regional quota (60 / 30 / 10)
    quotas = {region: int(total * cfg["weight"]) for region, cfg in REGIONS.items()}

    selected: List[dict] = []
    for region_name, jobs in combined.items():
        cap   = quotas[region_name]
        chunk = jobs[:cap] if len(jobs) >= cap else jobs
        selected.extend(chunk)

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
        len([j for j in selected if j["region"] == "Europe"]),
        len([j for j in selected if j["region"] == "Asia"]),
        len([j for j in selected if j["region"] == "USA_Canada"]),
        len([j for j in selected if j["region"] == "South_America"]),
        len([j for j in selected if j["region"] == "Middle_East"]),
        source_counts,
    )

    # Restore original SEARCH_KEYWORDS if they were extended for this run
    if original_keywords is not None:
        import config as _cfg
        _cfg.SEARCH_KEYWORDS = original_keywords

    return selected


def load_seen_ids(path) -> Set[str]:
    try:
        with open(str(path)) as f:
            return set(line.strip() for line in f if line.strip())
    except FileNotFoundError:
        return set()


def save_seen_ids(path, ids: Set[str]) -> None:
    with open(str(path), "w") as f:
        f.write("\n".join(sorted(ids)))


def _make_fingerprint(title: str, company: str) -> str:
    """Stable lowercase hash for deduplicating same job across sources and runs."""
    import hashlib
    key = f"{title.lower().strip()}::{company.lower().strip()}"
    return hashlib.md5(key.encode()).hexdigest()


def load_seen_fingerprints(path) -> Set[str]:
    try:
        with open(str(path)) as f:
            return set(line.strip() for line in f if line.strip())
    except FileNotFoundError:
        return set()


def save_seen_fingerprints(path, fps: Set[str]) -> None:
    with open(str(path), "w") as f:
        f.write("\n".join(sorted(fps)))
