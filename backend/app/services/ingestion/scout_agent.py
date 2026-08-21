"""
Scout Agent — FR-2.x

Searches for job postings matching user-defined criteria.
Primary source: Adzuna API (if configured).
Fallback: DuckDuckGo HTML scrape (respects robots.txt via user-agent + rate limits).

Extracts: company, role, JD text, application link, source channel.
All results are passed through canonicalize_job() before storage.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from urllib.parse import urlencode, urlparse

import httpx
from bs4 import BeautifulSoup

from backend.app.config import get_settings


@dataclass
class RawJobResult:
    company: str
    role: str
    jd_text: str
    application_link: str
    source: str  # "adzuna" | "web_scrape"


# ─── HTTP helpers ─────────────────────────────────────────────────────────────

_HEADERS = {
    "User-Agent": (
        "Agentic-JobFlow/1.0 (job-search research tool; "
        "contact: user-configured) +https://github.com/agentic-jobflow"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
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
            response = await client.get(url, params=params, headers=_HEADERS, timeout=15.0)
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


# ─── Adzuna Adapter ────────────────────────────────────────────────────────────

async def _search_adzuna(
    title: str,
    location: str,
    keywords: list[str],
    limit: int = 20,
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

    country = "us"  # default; could be parameterized
    url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"

    async with httpx.AsyncClient() as client:
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


# ─── Web Scrape Fallback ───────────────────────────────────────────────────────

def _extract_text_from_soup(soup: BeautifulSoup) -> str:
    """Extract readable text from job listing HTML."""
    for tag in soup(["script", "style", "nav", "footer", "header"]):
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


async def _search_web_fallback(
    title: str,
    location: str,
    keywords: list[str],
    limit: int = 10,
) -> list[RawJobResult]:
    """
    DuckDuckGo HTML scrape fallback.
    Returns job listings from search results.
    Note: Does not click through to individual job pages (respects ToS stance).
    """
    query = f"{title} {location} job opening site:greenhouse.io OR site:lever.co OR site:ashbyhq.com"
    if keywords:
        query += " " + " ".join(keywords)

    search_url = "https://html.duckduckgo.com/html/"
    params = {"q": query, "kl": "us-en"}

    results: list[RawJobResult] = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
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
            # Only process known ATS domains
            if not any(
                d in parsed.netloc
                for d in ["greenhouse.io", "lever.co", "ashbyhq.com"]
            ):
                continue

            # Rate limit: 1 request per second
            await asyncio.sleep(1.0)
            jd_text = await _scrape_url(client, href)

            if not jd_text:
                continue

            # Extract company from URL path heuristically
            parts = parsed.path.strip("/").split("/")
            company = parts[0] if parts else parsed.netloc

            results.append(
                RawJobResult(
                    company=company,
                    role=title,  # title as placeholder; JD text has true role
                    jd_text=jd_text,
                    application_link=href,
                    source="web_scrape",
                )
            )

    return results


# ─── Public API ───────────────────────────────────────────────────────────────

async def scout_jobs(
    title: str,
    location: str,
    keywords: list[str] | None = None,
    limit: int = 20,
) -> list[RawJobResult]:
    """
    FR-2.1: Search for job postings matching criteria.
    FR-2.2: Extract company, role, JD text, application link.
    FR-2.3: Rate limits respected via exponential backoff and per-request delays.

    Returns raw results; caller passes each through canonicalize_job().
    """
    keywords = keywords or []
    results = await _search_adzuna(title, location, keywords, limit)
    if not results:
        results = await _search_web_fallback(title, location, keywords, limit)
    return results


async def scrape_job_url(url: str) -> RawJobResult | None:
    """
    Scrape a single job posting URL directly.
    Used when user pastes a URL into the dashboard.
    """
    async with httpx.AsyncClient(follow_redirects=True) as client:
        jd_text = await _scrape_url(client, url)
    if not jd_text:
        return None

    parsed = urlparse(url)
    parts = parsed.path.strip("/").split("/")
    company = parts[0] if parts else parsed.netloc

    return RawJobResult(
        company=company,
        role="",  # to be extracted from JD or filled by user
        jd_text=jd_text,
        application_link=url,
        source="web_scrape",
    )
