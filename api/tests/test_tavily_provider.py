import asyncio
import pytest
from src.providers.tavily import (
    extract_domain,
    search_tavily,
    tavily_configured,
    TavilySearchResult,
    TavilyResponse,
)
from src.config.env import env

def test_extract_domain():
    assert extract_domain("https://www.reuters.com/technology/ai-update") == "reuters.com"
    assert extract_domain("https://techcrunch.com/2026/10/12/nvidia-chips/") == "techcrunch.com"
    assert extract_domain("https://subdomain.blog.example.org/post?q=1") == "subdomain.blog.example.org"
    assert extract_domain("invalid-url") == "Web Source"

def test_tavily_search_result_normalization():
    res = TavilySearchResult(
        url="https://news.ycombinator.com/item?id=123",
        title="Show HN: AI Agent",
        content="Long extracted article text here with details.",
        snippet="Short excerpt",
        publisher="news.ycombinator.com",
        published_date="2026-10-14",
        score=0.98,
        query="AI agents",
    )
    assert res.url == "https://news.ycombinator.com/item?id=123"
    assert res.publisher == "news.ycombinator.com"
    assert res.score == 0.98

def test_tavily_empty_query_rejected(monkeypatch):
    monkeypatch.setattr(env, "TAVILY_API_KEY", "test-tavily-key")
    with pytest.raises(Exception) as exc_info:
        asyncio.run(search_tavily("   "))
    assert "query cannot be empty" in str(exc_info.value).lower()

def test_tavily_live_or_skip():
    """If TAVILY_API_KEY is configured in the environment, perform a real search test."""
    if not tavily_configured():
        pytest.skip("TAVILY_API_KEY is not configured; skipping live provider call.")
    
    resp = asyncio.run(search_tavily("artificial intelligence robotics 2026", max_results=2))
    assert isinstance(resp, TavilyResponse)
    assert len(resp.results) > 0
    assert resp.results[0].url.startswith("http")
    assert resp.latency_ms > 0
