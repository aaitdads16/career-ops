"""
Free (no API key required) job scrapers to supplement Apify sources.

Sources
-------
- RemoteOK  : public JSON API  — remote internships globally
- Arbeitnow : public JSON API  — European jobs, internship-typed
- Jobteaser : Crawl4AI (async) — campus internships, EU-heavy
- Internshala: Crawl4AI (async)— India internships (Asia region)

Crawl4AI sources gracefully skip if the package is not installed.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

import requests

from config import REGIONS, SEARCH_KEYWORDS

logger = logging.getLogger(__name__)

# ── Shared constants ─────────────────────────────────────────────────────────

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "application/json, text/html, */*",
}

_INTERN_TERMS = {"intern", "internship", "stage", "trainee", "werkstudent", "praktikum", "stagaire"}

_EXCLUDED_LOCATIONS = [
    "france", "paris", "lyon", "marseille", "toulouse", "nice",
    "morocco", "maroc", "casablanca", "rabat",
    "egypt", "nigeria", "kenya", "south africa", "algerie", "algeria",
    "tunisia", "ghana", "senegal", "ethiopia", "tanzania",
    "cameroon", "ivory coast", "côte d'ivoire", "abidjan",
]


# ── Shared helpers (standalone — no import from scraper.py to avoid cycles) ──

def _is_excluded(location: str) -> bool:
    loc = (location or "").lower()
    return any(ex in loc for ex in _EXCLUDED_LOCATIONS)


def _parse_date(raw) -> Optional[datetime]:
    if not raw:
        return None
    try:
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(raw / 1000 if raw > 1e10 else raw,
                                          tz=timezone.utc)
        from dateutil import parser as dp
        dt = dp.parse(str(raw))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


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


def _is_intern_title(title: str) -> bool:
    t = title.lower()
    return any(term in t for term in _INTERN_TERMS)


# ── RemoteOK JSON API ────────────────────────────────────────────────────────

_REMOTEOK_TAGS = [
    "data-science-intern",
    "machine-learning-intern",
    "ai-intern",
    "data-analyst-intern",
    "intern",
]

_REMOTEOK_URL = "https://remoteok.com/api"


def _fetch_remoteok_tag(tag: str) -> List[dict]:
    """Fetch jobs for a single tag from RemoteOK API."""
    try:
        r = requests.get(
            _REMOTEOK_URL,
            params={"tag": tag},
            headers=_HEADERS,
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        # First element is always metadata — skip it
        return [item for item in data if isinstance(item, dict) and "position" in item]
    except Exception as exc:
        logger.warning("RemoteOK [%s] failed: %s", tag, exc)
        return []


def _normalize_remoteok(item: dict) -> Optional[dict]:
    job_id  = str(item.get("id") or item.get("slug") or "")
    title   = item.get("position") or ""
    company = item.get("company") or ""
    location = item.get("location") or "Remote"
    url     = item.get("url") or f"https://remoteok.com/remote-jobs/{job_id}"
    desc    = item.get("description") or ""
    posted  = item.get("epoch") or item.get("date") or ""
    tags    = item.get("tags") or []

    if not (title and company and url):
        return None
    if not _is_intern_title(title) and not any(_t in (t.lower() for t in tags) for _t in _INTERN_TERMS):
        return None
    if _is_excluded(location):
        return None

    return {
        "job_id":      f"remoteok_{job_id}",
        "source":      "RemoteOK",
        "title":       title,
        "company":     company,
        "location":    location if location and location.lower() != "worldwide" else "Remote / Worldwide",
        "region":      _infer_region(location) if location and location.lower() not in ("worldwide", "remote") else "USA_Canada",
        "url":         url,
        "description": desc,
        "posted_at":   _parse_date(posted),
        "posted_raw":  str(posted),
        "found_at":    datetime.now(tz=timezone.utc),
    }


def scrape_remoteok(seen_ids: Set[str]) -> Dict[str, List[dict]]:
    """Scrape RemoteOK for internships via public JSON API."""
    results: Dict[str, List[dict]] = {r: [] for r in REGIONS}
    seen_in_run: Set[str] = set()

    for tag in _REMOTEOK_TAGS:
        items = _fetch_remoteok_tag(tag)
        logger.info("  RemoteOK [%s] → %d raw items", tag, len(items))
        for item in items:
            norm = _normalize_remoteok(item)
            if norm is None:
                continue
            jid = norm["job_id"]
            if jid in seen_ids or jid in seen_in_run:
                continue
            seen_in_run.add(jid)
            results[norm["region"]].append(norm)
        time.sleep(1.5)  # be polite between tag requests

    total = sum(len(v) for v in results.values())
    logger.info("  RemoteOK total new: %d", total)
    return results


# ── Arbeitnow JSON API ───────────────────────────────────────────────────────

_ARBEITNOW_URL = "https://www.arbeitnow.com/api/job-board-api"
_ARBEITNOW_PAGES = 3


def _fetch_arbeitnow_page(page: int) -> List[dict]:
    try:
        r = requests.get(
            _ARBEITNOW_URL,
            params={"page": page},
            headers=_HEADERS,
            timeout=20,
        )
        r.raise_for_status()
        return r.json().get("data", [])
    except Exception as exc:
        logger.warning("Arbeitnow page %d failed: %s", page, exc)
        return []


def _normalize_arbeitnow(item: dict) -> Optional[dict]:
    job_id  = str(item.get("slug") or item.get("id") or "")
    title   = item.get("title") or ""
    company = item.get("company_name") or ""
    location = item.get("location") or ""
    url     = item.get("url") or ""
    desc    = item.get("description") or ""
    posted  = item.get("created_at") or ""
    job_types = [jt.lower() for jt in (item.get("job_types") or [])]
    tags    = [t.lower() for t in (item.get("tags") or [])]

    if not (title and company and url):
        return None

    # Must be internship-type or internship-titled
    is_intern = (
        "internship" in job_types
        or "intern" in job_types
        or _is_intern_title(title)
        or any(t in tags for t in _INTERN_TERMS)
    )
    if not is_intern:
        return None
    if _is_excluded(location):
        return None

    return {
        "job_id":      f"arbeitnow_{job_id}",
        "source":      "Arbeitnow",
        "title":       title,
        "company":     company,
        "location":    location or "Europe",
        "region":      _infer_region(location) if location else "Europe",
        "url":         url,
        "description": desc,
        "posted_at":   _parse_date(posted),
        "posted_raw":  str(posted),
        "found_at":    datetime.now(tz=timezone.utc),
    }


def scrape_arbeitnow(seen_ids: Set[str]) -> Dict[str, List[dict]]:
    """Scrape Arbeitnow for internships via public JSON API."""
    results: Dict[str, List[dict]] = {r: [] for r in REGIONS}
    seen_in_run: Set[str] = set()

    for page in range(1, _ARBEITNOW_PAGES + 1):
        items = _fetch_arbeitnow_page(page)
        logger.info("  Arbeitnow page %d → %d raw items", page, len(items))
        for item in items:
            norm = _normalize_arbeitnow(item)
            if norm is None:
                continue
            jid = norm["job_id"]
            if jid in seen_ids or jid in seen_in_run:
                continue
            seen_in_run.add(jid)
            results[norm["region"]].append(norm)

    total = sum(len(v) for v in results.values())
    logger.info("  Arbeitnow total new: %d", total)
    return results


# ── Crawl4AI scrapers (optional — graceful skip if package absent) ───────────

try:
    from crawl4ai import AsyncWebCrawler
    from crawl4ai.extraction_strategy import JsonCssExtractionStrategy
    _CRAWL4AI_AVAILABLE = True
except ImportError:
    _CRAWL4AI_AVAILABLE = False


_JOBTEASER_SCHEMA = {
    "name": "Jobteaser offers",
    "baseSelector": "article.job-list-item, .job-card, [data-testid='job-card'], .job-item",
    "fields": [
        {"name": "title",   "selector": "h2, h3, .job-title, [class*='title']",      "type": "text"},
        {"name": "company", "selector": ".company-name, [class*='company']",           "type": "text"},
        {"name": "location","selector": ".location, [class*='location'], [class*='city']","type": "text"},
        {"name": "url",     "selector": "a",                                            "type": "attribute", "attribute": "href"},
    ],
}

_INTERNSHALA_SCHEMA = {
    "name": "Internshala offers",
    "baseSelector": ".internship_meta, .individual_internship",
    "fields": [
        {"name": "title",   "selector": ".profile a, h3.heading",                      "type": "text"},
        {"name": "company", "selector": ".company_name a, .company-name",              "type": "text"},
        {"name": "location","selector": ".location_link, .location a",                 "type": "text"},
        {"name": "url",     "selector": ".profile a",                                  "type": "attribute", "attribute": "href"},
    ],
}

_JOBTEASER_URLS = [
    "https://www.jobteaser.com/en/internships?search[query]=data+science",
    "https://www.jobteaser.com/en/internships?search[query]=machine+learning",
    "https://www.jobteaser.com/en/internships?search[query]=artificial+intelligence",
]

_INTERNSHALA_URLS = [
    "https://internshala.com/internships/data-science-internship",
    "https://internshala.com/internships/machine-learning-internship",
]


async def _crawl_page(url: str, schema: dict, source_name: str) -> List[dict]:
    """Fetch one page with Crawl4AI and return extracted items."""
    try:
        strategy = JsonCssExtractionStrategy(schema, verbose=False)
        async with AsyncWebCrawler(verbose=False) as crawler:
            result = await crawler.arun(
                url=url,
                extraction_strategy=strategy,
                js_code="window.scrollTo(0, document.body.scrollHeight);",
                wait_for="css:.job-list-item, .internship_meta, .job-card",
                page_timeout=30000,
            )
        if result.success and result.extracted_content:
            import json
            items = json.loads(result.extracted_content)
            return items if isinstance(items, list) else []
    except Exception as exc:
        logger.warning("Crawl4AI [%s] %s failed: %s", source_name, url, exc)
    return []


def _normalize_crawled(item: dict, source: str, base_url: str = "") -> Optional[dict]:
    title   = (item.get("title") or "").strip()
    company = (item.get("company") or "").strip()
    location = (item.get("location") or "").strip()
    url     = (item.get("url") or "").strip()

    if not title:
        return None
    if url and not url.startswith("http"):
        url = base_url.rstrip("/") + "/" + url.lstrip("/")
    if not url:
        return None
    if not _is_intern_title(title):
        return None
    if _is_excluded(location):
        return None

    import hashlib
    job_id = hashlib.md5(f"{source}_{title}_{company}".encode()).hexdigest()[:16]

    return {
        "job_id":      f"{source.lower()}_{job_id}",
        "source":      source,
        "title":       title,
        "company":     company or "Unknown",
        "location":    location or "Europe",
        "region":      _infer_region(location) if location else "Europe",
        "url":         url,
        "description": "",
        "posted_at":   None,
        "posted_raw":  "",
        "found_at":    datetime.now(tz=timezone.utc),
    }


def _run_crawl4ai_scraper(
    urls: List[str], schema: dict, source: str, base_url: str, seen_ids: Set[str]
) -> Dict[str, List[dict]]:
    """Run async Crawl4AI scraper synchronously and return region-bucketed results."""
    results: Dict[str, List[dict]] = {r: [] for r in REGIONS}
    seen_in_run: Set[str] = set()

    async def _run_all():
        all_items = []
        for url in urls:
            items = await _crawl_page(url, schema, source)
            logger.info("  %s [%s] → %d raw items", source, url, len(items))
            all_items.extend(items)
            await asyncio.sleep(2)
        return all_items

    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        all_items = loop.run_until_complete(_run_all())
    except Exception as exc:
        logger.warning("%s async run failed: %s", source, exc)
        return results

    for item in all_items:
        norm = _normalize_crawled(item, source, base_url)
        if norm is None:
            continue
        jid = norm["job_id"]
        if jid in seen_ids or jid in seen_in_run:
            continue
        seen_in_run.add(jid)
        results[norm["region"]].append(norm)

    total = sum(len(v) for v in results.values())
    logger.info("  %s total new: %d", source, total)
    return results


def scrape_jobteaser(seen_ids: Set[str]) -> Dict[str, List[dict]]:
    if not _CRAWL4AI_AVAILABLE:
        logger.info("  Jobteaser skipped — crawl4ai not installed")
        return {r: [] for r in REGIONS}
    return _run_crawl4ai_scraper(
        _JOBTEASER_URLS, _JOBTEASER_SCHEMA, "Jobteaser",
        "https://www.jobteaser.com", seen_ids,
    )


def scrape_internshala(seen_ids: Set[str]) -> Dict[str, List[dict]]:
    if not _CRAWL4AI_AVAILABLE:
        logger.info("  Internshala skipped — crawl4ai not installed")
        return {r: [] for r in REGIONS}
    return _run_crawl4ai_scraper(
        _INTERNSHALA_URLS, _INTERNSHALA_SCHEMA, "Internshala",
        "https://internshala.com", seen_ids,
    )


# ── Aggregator ────────────────────────────────────────────────────────────────

def scrape_free_sources(seen_ids: Set[str]) -> Dict[str, List[dict]]:
    """
    Run all free scrapers and return a merged region dict.
    Each source is independent — failures are logged and skipped.
    """
    combined: Dict[str, List[dict]] = {r: [] for r in REGIONS}

    def _merge(src: Dict[str, List[dict]]):
        for region, jobs in src.items():
            combined[region].extend(jobs)

    logger.info("── Scraping RemoteOK (free JSON API) ────────────────")
    _merge(scrape_remoteok(seen_ids))

    logger.info("── Scraping Arbeitnow (free JSON API) ───────────────")
    _merge(scrape_arbeitnow(seen_ids))

    logger.info("── Scraping Jobteaser (Crawl4AI) ────────────────────")
    _merge(scrape_jobteaser(seen_ids))

    logger.info("── Scraping Internshala (Crawl4AI) ──────────────────")
    _merge(scrape_internshala(seen_ids))

    total = sum(len(v) for v in combined.values())
    source_counts: dict = {}
    for jobs in combined.values():
        for j in jobs:
            s = j["source"]
            source_counts[s] = source_counts.get(s, 0) + 1
    logger.info("Free sources total: %d | breakdown: %s", total, source_counts)

    return combined
