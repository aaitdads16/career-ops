"""
Multi-source internship scraper.
Sources: Indeed · LinkedIn · Glassdoor · Wellfound (startup jobs)
All routed through Apify actors, deduplicated, and quota-balanced by region.
"""

import logging
import random
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set

from apify_client import ApifyClient

from config import (
    ACTOR_GLASSDOOR,
    ACTOR_INDEED,
    ACTOR_LINKEDIN,
    ACTOR_WELLFOUND,
    APIFY_API_TOKEN,
    COMPANY_BLACKLIST,
    DATE_POSTED,
    GLASSDOOR_DAYS_OLD,
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


def scrape_linkedin(client: ApifyClient, seen_ids: Set[str]) -> Dict[str, List[dict]]:
    """Scrape LinkedIn for each region using public job search URLs."""
    results: Dict[str, List[dict]] = {r: [] for r in REGIONS}
    for region_name, cities in LINKEDIN_REGIONS.items():
        random.shuffle(cities)
        for city, _ in cities:
            urls = [linkedin_url(kw, city) for kw in SEARCH_KEYWORDS]
            items = _run_actor(client, ACTOR_LINKEDIN, {
                "urls":          urls,
                "scrapeCompany": False,
                "count":         RESULTS_PER_SEARCH,
            }, timeout=180)
            logger.info("  LinkedIn [%s] → %d", city, len(items))
            for item in items:
                norm = _normalize_linkedin(item, region_name)
                if norm and norm["job_id"] not in seen_ids:
                    results[region_name].append(norm)
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

def _normalize_wellfound(item: dict, region: str) -> Optional[dict]:
    job_id  = str(item.get("id") or item.get("jobId") or "")
    title   = item.get("title") or item.get("jobTitle") or ""
    company = (item.get("company") or {}).get("name") or item.get("companyName") or ""
    location= item.get("location") or item.get("jobLocation") or ""
    url     = item.get("url") or item.get("jobUrl") or ""
    desc    = item.get("description") or ""
    posted  = item.get("postedAt") or item.get("datePosted") or ""

    if not (title and company and url):
        return None
    if _is_excluded(location) or _is_blacklisted(company):
        return None

    if not job_id:
        job_id = url

    # Infer region from location text if not deterministic
    if not region:
        region = _infer_region(location)

    return {
        "job_id":      f"wellfound_{job_id}",
        "source":      "Wellfound",
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


def _infer_region(location: str) -> str:
    loc = location.lower()
    europe_hints = ["london","berlin","amsterdam","stockholm","zurich","barcelona",
                    "dublin","brussels","copenhagen","oslo","paris","rome","madrid",
                    "uk","de","nl","se","ch","es","ie","be","dk","no","eu","europe"]
    asia_hints   = ["singapore","tokyo","seoul","hong kong","bangalore","kuala lumpur",
                    "taipei","sg","jp","kr","hk","in","my","tw","asia"]
    for h in europe_hints:
        if h in loc:
            return "Europe"
    for h in asia_hints:
        if h in loc:
            return "Asia"
    return "USA_Canada"


def scrape_wellfound(client: ApifyClient, seen_ids: Set[str]) -> Dict[str, List[dict]]:
    """Scrape Wellfound — free actor, global startup internships."""
    results: Dict[str, List[dict]] = {r: [] for r in REGIONS}

    wellfound_searches = [
        ("data science", ""),
        ("machine learning", ""),
        ("AI", ""),
        ("data scientist", ""),
    ]
    for keyword, location in wellfound_searches:
        items = _run_actor(client, ACTOR_WELLFOUND, {
            "searchTerms": [keyword],
            "location":    location,
            "role":        "internship",
            "maxResults":  WELLFOUND_MAX,
        }, timeout=180)
        logger.info("  Wellfound '%s' → %d", keyword, len(items))
        for item in items:
            # Wellfound is global; infer region from location text
            norm = _normalize_wellfound(item, _infer_region(item.get("location", "")))
            if norm and norm["job_id"] not in seen_ids:
                results[norm["region"]].append(norm)
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
    seen: Set[str] = set()
    out = []
    for j in jobs:
        if j["job_id"] not in seen:
            seen.add(j["job_id"])
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


def scrape_all(seen_ids: Set[str]) -> List[dict]:
    """
    Full multi-source scrape. Returns deduplicated, quota-balanced, sorted jobs.
    Sources: Indeed + LinkedIn + Glassdoor + Wellfound.
    """
    if not APIFY_API_TOKEN:
        raise ValueError("APIFY_API_TOKEN is not set. Add it to your .env file.")

    client = ApifyClient(APIFY_API_TOKEN)

    logger.info("── Scraping Indeed ──────────────────────────────────")
    indeed_results    = scrape_indeed(client, seen_ids)

    logger.info("── Scraping LinkedIn ────────────────────────────────")
    linkedin_results  = scrape_linkedin(client, seen_ids)

    logger.info("── Scraping Glassdoor ───────────────────────────────")
    glassdoor_results = scrape_glassdoor(client, seen_ids)

    logger.info("── Scraping Wellfound ───────────────────────────────")
    wellfound_results = scrape_wellfound(client, seen_ids)

    # Merge all sources
    combined = _merge_sources(
        indeed_results, linkedin_results,
        glassdoor_results, wellfound_results,
    )

    # Remove jobs already in seen_ids (belt-and-suspenders)
    for region in combined:
        combined[region] = [j for j in combined[region] if j["job_id"] not in seen_ids]

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
        "Total new jobs: %d | EU=%d AS=%d US/CA=%d | by source: %s",
        len(selected),
        len([j for j in selected if j["region"] == "Europe"]),
        len([j for j in selected if j["region"] == "Asia"]),
        len([j for j in selected if j["region"] == "USA_Canada"]),
        source_counts,
    )
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
