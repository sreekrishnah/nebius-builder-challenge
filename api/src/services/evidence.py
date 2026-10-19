import asyncio
from datetime import datetime, timezone
import re
from typing import List, Dict, Any, Optional, Callable

from src.lib.logger import create_logger
from src.lib.news import search_news
from src.providers.intelligence import ResearchPlan, SourceReference
from src.providers.tavily import search_tavily, tavily_configured, TavilySearchResult

log = create_logger('evidence-layer')

ProgressReporter = Callable[[str], None]

def parse_iso_or_now(date_str: Optional[str]) -> datetime:
    if not date_str:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime.now(timezone.utc)

def convert_tavily_to_article(res: TavilySearchResult) -> Dict[str, Any]:
    """Converts a Tavily search result into the internal NewsArticle format for pipeline ingestion."""
    return {
        "url": res.url,
        "title": res.title,
        "snippet": res.snippet or res.content[:500],
        "publisher": res.publisher,
        "publishedAt": parse_iso_or_now(res.published_date),
        "language": "en",
        "country": "US",
        "imageUrl": None,
        "query": res.query,
    }

async def execute_multi_step_research(
    plan: ResearchPlan,
    report: Optional[ProgressReporter] = None
) -> Dict[str, Any]:
    """
    Executes an observable multi-step research workflow:
    Stage A (Plan): Already built by Nemotron.
    Stage B (Search): Execute targeted Tavily queries.
    Stage C (Normalize & Deduplicate): Canonical URL preservation.
    Stage D (Investigate gaps): Second-pass search for opposing/risk evidence.
    Stage E (Final evidence set): Structured articles and source provenance.
    """
    articles: List[Dict[str, Any]] = []
    sources: List[SourceReference] = []
    seen_urls = set()
    failures: List[str] = []
    activity_trace: List[Dict[str, Any]] = []

    def log_step(stage: str, message: str, meta: Optional[Dict[str, Any]] = None):
        if report:
            report(message)
        activity_trace.append({
            "stage": stage,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **(meta or {})
        })

    log_step("research_plan", f"Prepared research plan with {len(plan.target_queries)} targeted queries", {
        "queries": plan.target_queries,
        "primaryFocus": plan.primary_focus
    })

    # Stage B: Search
    for idx, query in enumerate(plan.target_queries):
        log_step("tavily_search", f"Searching Tavily ({idx + 1} of {len(plan.target_queries)}): '{query}'")
        try:
            if tavily_configured():
                tavily_resp = await search_tavily(query, search_depth="basic", max_results=5)
                for res in tavily_resp.results:
                    if res.url not in seen_urls:
                        seen_urls.add(res.url)
                        articles.append(convert_tavily_to_article(res))
                        sources.append(SourceReference(
                            url=res.url,
                            title=res.title,
                            publisher=res.publisher,
                            published_date=res.published_date,
                            query=query
                        ))
            else:
                # Fallback to existing news engine if Tavily key is absent
                news_res = await search_news(query, days=7)
                for item in news_res:
                    if item['url'] not in seen_urls:
                        seen_urls.add(item['url'])
                        articles.append(item)
                        sources.append(SourceReference(
                            url=item['url'],
                            title=item['title'],
                            publisher=item.get('publisher', 'Web'),
                            published_date=item.get('publishedAt').isoformat() if hasattr(item.get('publishedAt'), 'isoformat') else str(item.get('publishedAt')),
                            query=query
                        ))
        except Exception as err:
            err_msg = str(err)
            failures.append(f"Query '{query}' failed: {err_msg}")
            log.warn("query search failed", {"query": query, "err": err_msg})

        # Polite rate-spacing
        await asyncio.sleep(0.8)

    log_step("evidence_normalized", f"Retrieved and normalized {len(articles)} distinct evidence sources", {
        "sourceCount": len(sources),
        "failuresCount": len(failures)
    })

    # Stage D: Second-pass investigation if counter-evidence query is present
    if plan.opposing_evidence_query and tavily_configured():
        opposing_q = plan.opposing_evidence_query.strip()
        log_step("investigate_counter_evidence", f"Investigating counter-evidence and risk factors: '{opposing_q}'")
        try:
            opposing_resp = await search_tavily(opposing_q, search_depth="basic", max_results=3)
            for res in opposing_resp.results:
                if res.url not in seen_urls:
                    seen_urls.add(res.url)
                    articles.append(convert_tavily_to_article(res))
                    sources.append(SourceReference(
                        url=res.url,
                        title=res.title,
                        publisher=res.publisher,
                        published_date=res.published_date,
                        query=opposing_q
                    ))
        except Exception as err:
            log.warn("counter-evidence search failed", {"query": opposing_q, "err": str(err)})

    log_step("evidence_finalized", f"Final evidence package contains {len(articles)} grounded sources across {len(plan.target_queries)} search directions")

    return {
        "articles": articles,
        "sources": sources,
        "activity_trace": activity_trace,
        "errors": failures
    }
