import asyncio
import time
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse
import httpx
from pydantic import BaseModel, Field

from src.config.env import env
from src.lib.errors import AppError, errors
from src.lib.logger import create_logger

log = create_logger('tavily')

TAVILY_API_ENDPOINT = "https://api.tavily.com/search"
TAVILY_TIMEOUT_SECONDS = 15.0

class TavilySearchResult(BaseModel):
    url: str
    title: str
    content: str
    snippet: Optional[str] = None
    publisher: str
    published_date: Optional[str] = None
    score: Optional[float] = None
    query: str

class TavilyResponse(BaseModel):
    query: str
    results: List[TavilySearchResult]
    latency_ms: float

def tavily_configured() -> bool:
    return bool(env.TAVILY_API_KEY and env.TAVILY_API_KEY.strip())

def extract_domain(url: str) -> str:
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        return domain or "Web Source"
    except Exception:
        return "Web Source"

async def search_tavily(
    query: str,
    search_depth: Optional[str] = None,
    max_results: Optional[int] = None,
    include_domains: Optional[List[str]] = None,
    exclude_domains: Optional[List[str]] = None,
) -> TavilyResponse:
    if not tavily_configured():
        raise errors.source("Tavily API key is not configured. Set TAVILY_API_KEY in .env.")

    q = query.strip()
    if not q:
        raise errors.validation("Tavily search query cannot be empty.")

    depth = search_depth or env.TAVILY_SEARCH_DEPTH
    max_r = max_results or env.TAVILY_MAX_RESULTS

    payload: Dict[str, Any] = {
        "api_key": env.TAVILY_API_KEY,
        "query": q,
        "search_depth": depth,
        "max_results": max_r,
        "include_answer": False,
        "include_images": False,
        "include_raw_content": False,
    }

    if include_domains:
        payload["include_domains"] = include_domains
    if exclude_domains:
        payload["exclude_domains"] = exclude_domains

    start_t = time.time()
    try:
        async with httpx.AsyncClient(timeout=TAVILY_TIMEOUT_SECONDS) as client:
            resp = await client.post(TAVILY_API_ENDPOINT, json=payload)

        latency = (time.time() - start_t) * 1000

        if resp.status_code == 401 or resp.status_code == 403:
            log.error("tavily authentication error", {"status": resp.status_code})
            raise AppError("TAVILY_AUTH_ERROR", 401, "Tavily authentication failed. Check your API key.")

        if resp.status_code == 429:
            log.warn("tavily rate limit reached")
            raise AppError("TAVILY_RATE_LIMIT", 429, "Tavily rate limit exceeded. Please retry later.")

        if resp.status_code >= 400:
            log.error("tavily request failed", {"status": resp.status_code, "text": resp.text[:200]})
            raise AppError("TAVILY_ERROR", resp.status_code, f"Tavily search failed with status {resp.status_code}.")

        data = resp.json()
        raw_results = data.get("results", [])

        normalized: List[TavilySearchResult] = []
        seen_urls = set()

        for r in raw_results:
            url = r.get("url", "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            raw_title = r.get("title", "").strip() or extract_domain(url)
            content = r.get("content", "").strip()
            snippet = content[:500] if content else raw_title

            normalized.append(
                TavilySearchResult(
                    url=url,
                    title=raw_title,
                    content=content,
                    snippet=snippet,
                    publisher=extract_domain(url),
                    published_date=r.get("published_date"),
                    score=r.get("score"),
                    query=q,
                )
            )

        log.info("tavily search completed", {"query": q, "results": len(normalized), "latencyMs": round(latency, 1)})
        return TavilyResponse(query=q, results=normalized, latency_ms=latency)

    except httpx.TimeoutException:
        latency = (time.time() - start_t) * 1000
        log.error("tavily search timed out", {"query": q, "latencyMs": latency})
        raise AppError("TAVILY_TIMEOUT", 504, f"Tavily search timed out after {TAVILY_TIMEOUT_SECONDS}s.")
    except (AppError, Exception) as e:
        if isinstance(e, AppError):
            raise
        log.error("tavily unexpected error", {"err": str(e)})
        raise errors.source(f"Tavily search encountered an error: {str(e)}")

async def test_tavily_connection() -> Dict[str, Any]:
    """Test Tavily API connectivity and key validity."""
    if not tavily_configured():
        return {
            "status": "not_configured",
            "message": "TAVILY_API_KEY is not configured",
            "ok": False,
        }
    try:
        resp = await search_tavily("latest developments in artificial intelligence", max_results=1)
        return {
            "status": "healthy",
            "ok": True,
            "latencyMs": round(resp.latency_ms, 1),
            "resultCount": len(resp.results),
            "sampleTitle": resp.results[0].title if resp.results else None,
        }
    except Exception as err:
        return {
            "status": "error",
            "ok": False,
            "error": str(err),
        }
