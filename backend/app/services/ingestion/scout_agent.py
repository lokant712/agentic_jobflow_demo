"""
Scout Agent — FR-2.x

Searches for live job postings matching user-defined criteria across multiple real sources:
1. LinkedIn Public Guest Job Search (zero credentials required)
2. Remotive Live Tech & Engineering Job API (zero credentials required)
3. Adzuna Job API (if configured in .env)
4. Multi-ATS Web Search (DuckDuckGo query for Greenhouse, Lever, Ashby, Naukri)

Extracts: company, role, JD text, application link, source channel.
All results are passed through canonicalize_job() before storage.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from urllib.parse import quote_plus, urlencode, urlparse

import httpx
from bs4 import BeautifulSoup

from backend.app.config import get_settings

log = logging.getLogger("jobflow.scout")


@dataclass
class RawJobResult:
    company: str
    role: str
    jd_text: str
    application_link: str
    source: str  # "linkedin" | "remotive" | "adzuna" | "web_scrape"


# ─── HTTP Helpers ─────────────────────────────────────────────────────────────

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


async def _get_with_retry(
    client: httpx.AsyncClient,
    url: str,
    params: dict | None = None,
    max_retries: int = 3,
) -> httpx.Response:
    """HTTP GET with exponential backoff. FR-2.3: respects rate limits."""
    delay = 1.0
    for attempt in range(max_retries):
        try:
            response = await client.get(url, params=params, headers=_HEADERS, timeout=12.0)
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", delay * 2))
                await asyncio.sleep(retry_after)
                continue
            response.raise_for_status()
            return response
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(delay)
            delay *= 2
    raise RuntimeError("Max retries exceeded")


def _extract_text_from_soup(soup: BeautifulSoup) -> str:
    """Extract clean readable text from job listing HTML."""
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "svg"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    return re.sub(r"\s+", " ", text)[:5000]


async def _scrape_url(client: httpx.AsyncClient, url: str) -> str:
    """Fetch and clean text from a job posting URL."""
    try:
        resp = await _get_with_retry(client, url)
        soup = BeautifulSoup(resp.text, "lxml")
        return _extract_text_from_soup(soup)
    except Exception:
        return ""


# ─── 1. LinkedIn Public Guest Job Search ─────────────────────────────────────

async def _search_linkedin(
    title: str,
    location: str = "Remote",
    limit: int = 10,
) -> list[RawJobResult]:
    """
    Scrapes LinkedIn's public guest job search API.
    Zero authentication or session cookies required.
    """
    search_url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    params = {
        "keywords": title,
        "location": location or "Remote",
        "start": 0,
    }

    results: list[RawJobResult] = []
    async with httpx.AsyncClient(headers=_HEADERS, follow_redirects=True, timeout=12.0) as client:
        try:
            resp = await _get_with_retry(client, search_url, params=params)
            soup = BeautifulSoup(resp.text, "lxml")
            cards = soup.select("li")[:limit]

            for card in cards:
                title_el = card.select_one(".base-search-card__title")
                company_el = card.select_one(".base-search-card__subtitle a, .base-search-card__subtitle")
                link_el = card.select_one("a.base-card__full-link, a")

                role = title_el.get_text(strip=True) if title_el else title
                company = company_el.get_text(strip=True) if company_el else "Unknown"
                raw_link = link_el.get("href", "") if link_el else ""

                # Strip tracking params from LinkedIn URL
                clean_link = raw_link.split("?")[0] if raw_link else ""

                if clean_link and company != "Unknown":
                    # Generate concise JD placeholder; will be augmented on scrape
                    results.append(
                        RawJobResult(
                            company=company,
                            role=role,
                            jd_text=(
                                f"{company} is hiring a {role} in {location}. "
                                f"Apply online at {clean_link}. Key requirements include {title} experience."
                            ),
                            application_link=clean_link,
                            source="linkedin",
                        )
                    )
        except Exception as exc:
            log.warning(f"LinkedIn search warning: {exc}")

    return results


# ─── 2. Remotive Live Tech Jobs API ──────────────────────────────────────────

async def _search_remotive(
    title: str,
    keywords: list[str],
    limit: int = 10,
) -> list[RawJobResult]:
    """
    Queries Remotive's open remote tech jobs API.
    Zero authentication required.
    """
    query = " ".join([title] + keywords)
    url = f"https://remotive.com/api/remote-jobs?search={quote_plus(query)}&limit={limit}"

    results: list[RawJobResult] = []
    async with httpx.AsyncClient(headers=_HEADERS, follow_redirects=True, timeout=12.0) as client:
        try:
            resp = await _get_with_retry(client, url)
            data = resp.json()
            jobs = data.get("jobs", [])[:limit]

            for job in jobs:
                soup = BeautifulSoup(job.get("description", ""), "lxml")
                clean_jd = _extract_text_from_soup(soup)

                results.append(
                    RawJobResult(
                        company=job.get("company_name", "Unknown"),
                        role=job.get("title", title),
                        jd_text=clean_jd or f"Job opening for {job.get('title')} at {job.get('company_name')}.",
                        application_link=job.get("url", ""),
                        source="remotive",
                    )
                )
        except Exception as exc:
            log.warning(f"Remotive search warning: {exc}")

    return results


# ─── 3. Adzuna Job API ────────────────────────────────────────────────────────

async def _search_adzuna(
    title: str,
    location: str,
    keywords: list[str],
    limit: int = 15,
) -> list[RawJobResult]:
    settings = get_settings()
    if not settings.adzuna_app_id or not settings.adzuna_app_key:
        return []

    query = " ".join([title] + keywords)
    params = {
        "app_id": settings.adzuna_app_id,
        "app_key": settings.adzuna_app_key,
        "results_per_page": min(limit, 50),
        "what": query,
        "where": location,
        "content-type": "application/json",
    }

    country = "us"
    url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"

    async with httpx.AsyncClient(headers=_HEADERS, timeout=12.0) as client:
        try:
            resp = await _get_with_retry(client, url, params=params)
            data = resp.json()
        except Exception:
            return []

    results = []
    for item in data.get("results", []):
        results.append(
            RawJobResult(
                company=item.get("company", {}).get("display_name", "Unknown"),
                role=item.get("title", ""),
                jd_text=item.get("description", ""),
                application_link=item.get("redirect_url", ""),
                source="adzuna",
            )
        )
    return results


# ─── 4. Multi-ATS Web Search (DuckDuckGo Fallback) ─────────────────────────────

async def _search_web_fallback(
    title: str,
    location: str,
    keywords: list[str],
    limit: int = 10,
) -> list[RawJobResult]:
    """
    DuckDuckGo search targeting live Greenhouse, Lever, Ashby, and Naukri listings.
    """
    query = f"{title} {location} job opening site:greenhouse.io OR site:lever.co OR site:ashbyhq.com OR site:naukri.com"
    if keywords:
        query += " " + " ".join(keywords[:3])

    search_url = "https://html.duckduckgo.com/html/"
    params = {"q": query, "kl": "us-en"}

    results: list[RawJobResult] = []
    async with httpx.AsyncClient(headers=_HEADERS, follow_redirects=True, timeout=12.0) as client:
        try:
            resp = await _get_with_retry(client, search_url, params=params)
        except Exception:
            return results

        soup = BeautifulSoup(resp.text, "lxml")
        links = soup.select("a.result__url, .result__title a")[:limit]

        for link in links:
            href = link.get("href", "")
            if not href.startswith("http"):
                continue
            parsed = urlparse(href)
            if not any(d in parsed.netloc for d in ["greenhouse.io", "lever.co", "ashbyhq.com", "naukri.com", "linkedin.com"]):
                continue

            await asyncio.sleep(0.5)
            jd_text = await _scrape_url(client, href)
            if not jd_text:
                continue

            parts = parsed.path.strip("/").split("/")
            company = parts[0] if parts else parsed.netloc

            results.append(
                RawJobResult(
                    company=company,
                    role=title,
                    jd_text=jd_text,
                    application_link=href,
                    source="web_scrape",
                )
            )

    return results


# ─── Public API ───────────────────────────────────────────────────────────────

async def scout_jobs(
    title: str,
    location: str = "Remote",
    keywords: list[str] | None = None,
    limit: int = 20,
) -> list[RawJobResult]:
    """
    Searches across LinkedIn, Remotive, Adzuna, and ATS search engines.
    Aggregates and deduplicates results before returning.
    """
    keywords = keywords or []
    results: list[RawJobResult] = []

    # 1. Search LinkedIn Public Jobs
    try:
        li_results = await _search_linkedin(title, location, limit=min(limit, 10))
        results.extend(li_results)
    except Exception as exc:
        log.warning(f"LinkedIn scout failed: {exc}")

    # 2. Search Remotive Tech Jobs
    try:
        rem_results = await _search_remotive(title, keywords, limit=min(limit, 10))
        results.extend(rem_results)
    except Exception as exc:
        log.warning(f"Remotive scout failed: {exc}")

    # 3. Search Adzuna (if configured)
    adz_results = await _search_adzuna(title, location, keywords, limit)
    results.extend(adz_results)

    # 4. Fallback search if still empty
    if not results:
        results = await _search_web_fallback(title, location, keywords, limit)

    # Deduplicate by company + role
    seen = set()
    deduped = []
    for r in results:
        key = f"{r.company.lower().strip()}||{r.role.lower().strip()}"
        if key not in seen and r.company and r.application_link:
            seen.add(key)
            deduped.append(r)

    log.info(f"Scout Agent: Discovered {len(deduped)} active jobs across LinkedIn, Remotive, and web feeds")
    return deduped[:limit]


async def scrape_job_url(url: str) -> RawJobResult | None:
    """
    Scrape a single job posting URL directly.
    """
    async with httpx.AsyncClient(headers=_HEADERS, follow_redirects=True, timeout=15.0) as client:
        jd_text = await _scrape_url(client, url)
    if not jd_text:
        return None

    parsed = urlparse(url)
    parts = parsed.path.strip("/").split("/")
    company = parts[0] if parts else parsed.netloc

    return RawJobResult(
        company=company,
        role="",
        jd_text=jd_text,
        application_link=url,
        source="web_scrape",
    )
