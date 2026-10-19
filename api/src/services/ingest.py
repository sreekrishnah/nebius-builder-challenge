import hashlib
from typing import List, Dict, Any, Optional, Set, Callable
from pydantic import BaseModel
from datetime import datetime, timezone, timedelta

from src.ai.guard import injection_signals, log_blocked, sanitize_untrusted
from src.ai.tasks import extract_development, resolve_story, StoryCandidate
from src.lib.errors import AppError, errors
from src.lib.gemini import embed_text, embed_texts
from src.lib.news import NewsArticle
from src.lib.logger import create_logger
from src.lib.supabase import rpc_rows, supabase, unwrap, unwrap_maybe, unwrap_void

log = create_logger('ingest')

STORY_LOOKBACK_DAYS = 180
STORY_CANDIDATES = 5
SAME_EVENT_SIMILARITY = 0.9
MIN_TITLE_LENGTH = 15
MAX_CONSECUTIVE_PROVIDER_FAILURES = 3

class IngestStats(BaseModel):
    received: int = 0
    alreadyKnown: int = 0
    rejected: int = 0
    newStories: int = 0
    newDevelopments: int = 0
    corroborations: int = 0
    noChange: int = 0
    failed: int = 0

def empty_stats() -> IngestStats:
    return IngestStats()

class StoryRow(BaseModel):
    id: str
    title: str
    summary: Optional[str] = None
    canonical_key: str

class DevelopmentRow(BaseModel):
    id: str
    headline: str
    new_state: str
    occurred_at: str

def slug(text: str) -> str:
    import re
    lowered = text.lower()
    lowered = re.sub(r'[^a-z0-9]+', '-', lowered)
    lowered = re.sub(r'^-|-$', '', lowered)
    return lowered[:60]

def short_hash(text: str) -> str:
    return hashlib.sha1(text.encode('utf-8')).hexdigest()[:10]

def evidence_strength(publishers: int) -> str:
    if publishers >= 3:
        return 'widely_reported'
    elif publishers == 2:
        return 'corroborated'
    else:
        return 'single_source'

def acceptable(article: NewsArticle) -> bool:
    # Use timezone-aware comparison
    now_utc = datetime.now(timezone.utc)
    return len(article.title) >= MIN_TITLE_LENGTH and article.publishedAt <= now_utc + timedelta(hours=1)

def screened(article: NewsArticle) -> Optional[NewsArticle]:
    sig_text = f"{article.title} {article.snippet or ''}"
    signals = injection_signals(sig_text)
    if len(signals) > 0:
        log_blocked('article', signals)
        return None
        
    return NewsArticle(
        url=article.url,
        title=sanitize_untrusted(article.title, 300),
        snippet=sanitize_untrusted(article.snippet, 700) if article.snippet else None,
        publisher=sanitize_untrusted(article.publisher, 80),
        publishedAt=article.publishedAt,
        language=article.language,
        country=article.country,
        imageUrl=article.imageUrl,
        query=article.query
    )

async def insert_article(article: NewsArticle) -> str:
    insert_data = {
        "external_url": article.url,
        "title": article.title,
        "snippet": article.snippet,
        "publisher": article.publisher,
        "published_at": article.publishedAt.isoformat().replace('+00:00', 'Z') if article.publishedAt.tzinfo else article.publishedAt.isoformat() + "Z",
        "language": article.language,
        "source_country": article.country,
        "image_url": article.imageUrl,
        "gdelt_query": article.query
    }
    
    res = supabase.table('articles').insert(insert_data).select('id').execute()
    row = unwrap('articles.insert', res)
    row = row[0] if isinstance(row, list) else row
    return row['id']

async def find_candidates(vector: List[float]) -> List[StoryCandidate]:
    since = (datetime.utcnow() - timedelta(days=STORY_LOOKBACK_DAYS)).isoformat() + "Z"
    stories = await rpc_rows('match_stories', {
        "p_embedding": vector,
        "p_limit": STORY_CANDIDATES,
        "p_since": since
    })
    
    candidates = []
    for story in stories:
        res = supabase.table('developments').select('new_state')\
            .eq('story_id', story['id']).order('occurred_at', desc=True).limit(1).maybe_single().execute()
        latest = unwrap_maybe('developments.latest', res)
        candidates.append(StoryCandidate(
            id=story['id'],
            title=story['title'],
            summary=story.get('summary'),
            latestState=latest['new_state'] if latest else None
        ))
    return candidates

async def create_story(article: NewsArticle, resolved: Any) -> StoryRow:
    emb_text = f"{resolved.title}. {resolved.summary}"
    embedding = await embed_text(emb_text, 'document')
    
    published_iso = article.publishedAt.isoformat().replace('+00:00', 'Z') if article.publishedAt.tzinfo else article.publishedAt.isoformat() + "Z"
    
    insert_data = {
        "canonical_key": f"{slug(resolved.title)}-{short_hash(resolved.title + article.url)}",
        "title": resolved.title,
        "summary": resolved.summary,
        "category": resolved.category,
        "geography": resolved.geography,
        "entities_json": resolved.entities,
        "first_seen_at": published_iso,
        "last_updated_at": published_iso,
        "status": 'active',
        "embedding": embedding
    }
    
    res = supabase.table('stories').insert(insert_data).select('id, title, summary, canonical_key').execute()
    row = unwrap('stories.insert', res)
    row = row[0] if isinstance(row, list) else row
    return StoryRow(**row)

async def load_history(story_id: str) -> List[DevelopmentRow]:
    select = 'id, headline, new_state, occurred_at'
    
    res_recent = supabase.table('developments').select(select).eq('story_id', story_id).order('occurred_at', desc=True).limit(4).execute()
    res_first = supabase.table('developments').select(select).eq('story_id', story_id).order('occurred_at', desc=False).limit(1).execute()
    
    rows_data = unwrap('developments.history', res_recent)
    rows_data = rows_data if isinstance(rows_data, list) else [rows_data]
    rows = [DevelopmentRow(**r) for r in reversed(rows_data)]
    
    first_data = unwrap('developments.origin', res_first)
    first_data = first_data if isinstance(first_data, list) else [first_data]
    
    origin = DevelopmentRow(**first_data[0]) if len(first_data) > 0 else None
    
    if origin and not any(r.id == origin.id for r in rows):
        return [origin] + rows
    return rows

async def attach_source(story_id: str, development_id: Optional[str], article_id: str, article: NewsArticle) -> None:
    published_iso = article.publishedAt.isoformat().replace('+00:00', 'Z') if article.publishedAt.tzinfo else article.publishedAt.isoformat() + "Z"
    
    insert_data = {
        "story_id": story_id,
        "development_id": development_id,
        "article_id": article_id,
        "url": article.url,
        "title": article.title,
        "publisher": article.publisher,
        "published_at": published_iso
    }
    res = supabase.table('story_sources').insert(insert_data).execute()
    unwrap_void('story_sources.insert', res)

async def corroborate(development_id: str, story_id: str, article_id: str, article: NewsArticle) -> None:
    await attach_source(story_id, development_id, article_id, article)
    
    res = supabase.table('story_sources').select('publisher').eq('development_id', development_id).execute()
    sources = unwrap('story_sources.count', res)
    sources = sources if isinstance(sources, list) else [sources]
    
    publishers = len(set(s['publisher'] for s in sources if s.get('publisher')))
    
    upd_data = {
        "source_count": len(sources),
        "independent_source_count": publishers,
        "evidence_strength": evidence_strength(publishers)
    }
    res_upd = supabase.table('developments').update(upd_data).eq('id', development_id).execute()
    unwrap_void('developments.corroborate', res_upd)

async def process_article(article: NewsArticle, vector: List[float], article_id: str, stats: IngestStats) -> str:
    candidates = await find_candidates(vector)
    
    class _ArticleInput(BaseModel):
        title: str
        snippet: Optional[str]
        publisher: str
        
    article_input = _ArticleInput(title=article.title, snippet=article.snippet, publisher=article.publisher)
    resolved = await resolve_story(article_input, candidates)
    
    matched = None
    if resolved.relationship == 'same_story':
        matched = next((c for c in candidates if c.id == resolved.story_id), None)
        
    is_new_story = False
    if matched:
        story_id = matched.id
        story_title = matched.title
        story_summary = matched.summary
    else:
        story = await create_story(article, resolved)
        story_id = story.id
        story_title = story.title
        story_summary = story.summary
        is_new_story = True
        stats.newStories += 1
        
    history = [] if is_new_story else await load_history(story_id)
    history_mapped = [{"occurredAt": h.occurred_at, "headline": h.headline, "newState": h.new_state} for h in history]
    
    published_iso = article.publishedAt.isoformat().replace('+00:00', 'Z') if article.publishedAt.tzinfo else article.publishedAt.isoformat() + "Z"
    
    extraction = await extract_development(
        storyTitle=story_title,
        storySummary=story_summary,
        history=history_mapped,
        articles=[{
            "url": article.url,
            "title": article.title,
            "snippet": article.snippet,
            "publisher": article.publisher,
            "publishedAt": published_iso
        }]
    )
    
    if not extraction.is_meaningful_change and not is_new_story:
        await attach_source(story_id, None, article_id, article)
        return 'no_change'
        
    development_text = f"{extraction.headline}. {extraction.what_changed}"
    development_vector = await embed_text(development_text, 'document')
    
    if not is_new_story:
        similar = await rpc_rows('match_developments', {
            "p_embedding": development_vector,
            "p_story_id": story_id,
            "p_limit": 1,
            "p_since": None
        })
        if len(similar) > 0 and similar[0]['similarity'] >= SAME_EVENT_SIMILARITY:
            await corroborate(similar[0]['id'], story_id, article_id, article)
            return 'corroboration'
            
    continuity = 'new' if is_new_story else ('updated' if extraction.continuity == 'new' else extraction.continuity)
    
    insert_dev_data = {
        "story_id": story_id,
        "canonical_event_key": f"{slug(story_title)}-{short_hash(extraction.headline + article.url)}",
        "headline": extraction.headline,
        "summary": extraction.summary,
        "development_type": continuity.upper(),
        "continuity": continuity,
        "occurred_at": published_iso,
        "first_reported_at": datetime.utcnow().isoformat() + "Z",
        "source_count": 1,
        "independent_source_count": 1,
        "evidence_strength": evidence_strength(1),
        "world_significance": extraction.world_significance,
        "significance_reason": extraction.significance_reason,
        "what_changed": extraction.what_changed,
        "previous_state": extraction.previous_state,
        "new_state": extraction.new_state,
        "evidence_json": {"articleUrls": [article.url]},
        "embedding": development_vector
    }
    
    res_dev = supabase.table('developments').insert(insert_dev_data).select('id').execute()
    dev_row = unwrap('developments.insert', res_dev)
    dev_row = dev_row[0] if isinstance(dev_row, list) else dev_row
    
    await attach_source(story_id, dev_row['id'], article_id, article)
    
    story_vector = await embed_text(f"{story_title}. {extraction.new_state}", 'document')
    
    upd_story_data = {
        "summary": extraction.new_state,
        "last_updated_at": published_iso,
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "embedding": story_vector
    }
    res_st_upd = supabase.table('stories').update(upd_story_data).eq('id', story_id).execute()
    unwrap_void('stories.touch', res_st_upd)
    
    return 'story' if is_new_story else 'development'

KNOWN_URL_BATCH = 40

async def find_known_urls(urls: List[str]) -> Set[str]:
    known = set()
    for i in range(0, len(urls), KNOWN_URL_BATCH):
        batch = urls[i:i+KNOWN_URL_BATCH]
        res = supabase.table('articles').select('external_url').in_('external_url', batch).execute()
        rows = unwrap('articles.known', res)
        rows = rows if isinstance(rows, list) else [rows]
        for r in rows:
            known.add(r['external_url'])
    return known

async def ingest_articles(raw: List[NewsArticle], on_progress: Optional[Callable[[int, int], None]] = None) -> IngestStats:
    stats = empty_stats()
    stats.received = len(raw)
    
    unique: Dict[str, NewsArticle] = {}
    for candidate in raw:
        article = screened(candidate) if acceptable(candidate) else None
        if not article:
            stats.rejected += 1
        elif article.url not in unique:
            unique[article.url] = article
            
    if len(unique) == 0:
        return stats
        
    urls_list = list(unique.keys())
    known_urls = await find_known_urls(urls_list)
    stats.alreadyKnown = len(known_urls)
    
    fresh = [a for a in unique.values() if a.url not in known_urls]
    fresh.sort(key=lambda a: a.publishedAt.timestamp())
    
    if len(fresh) == 0:
        return stats
        
    vectors = await embed_texts([a.title for a in fresh], 'query')
    consecutive_provider_failures = 0
    
    for i, article in enumerate(fresh):
        if on_progress:
            on_progress(i, len(fresh))
            
        article_id = None
        try:
            article_id = await insert_article(article)
            outcome = await process_article(article, vectors[i], article_id, stats)
            consecutive_provider_failures = 0
            
            if outcome in ('story', 'development'):
                stats.newDevelopments += 1
            elif outcome == 'corroboration':
                stats.corroborations += 1
            else:
                stats.noChange += 1
        except Exception as err:
            stats.failed += 1
            log.error('article ingestion failed', {"url": article.url, "err": str(err)})
            
            if article_id:
                supabase.table('articles').delete().eq('id', article_id).execute()
                
            provider_down = isinstance(err, AppError) and err.code in ('MODEL_UNAVAILABLE', 'EMBEDDING_UNAVAILABLE')
            consecutive_provider_failures = (consecutive_provider_failures + 1) if provider_down else 0
            
            if consecutive_provider_failures >= MAX_CONSECUTIVE_PROVIDER_FAILURES:
                raise err
                
    log.info('ingest complete', stats.dict())
    return stats
