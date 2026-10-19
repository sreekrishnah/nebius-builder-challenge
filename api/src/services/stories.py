from typing import List, Optional, Dict, Any, Union
from pydantic import BaseModel
from datetime import datetime
import json

from src.domain.types import Continuity
from src.lib.errors import errors
from src.lib.logger import create_logger
from src.lib.supabase import supabase, unwrap, unwrap_maybe, unwrap_void, rpc_rows

log = create_logger('stories')

AMBITION_LINK_MIN = 0.55

class SourceSummary(BaseModel):
    name: str
    url: str
    title: Optional[str] = None

class TimelineDevelopment(BaseModel):
    id: str
    dayNumber: int
    headline: str
    summary: Optional[str] = None
    whatChanged: str
    previousState: str
    newState: str
    continuity: Optional[Continuity] = None
    occurredAt: str
    worldSignificance: Optional[float] = None
    evidenceStrength: Optional[str] = None
    sources: List[SourceSummary]

class TrackedStorySummary(BaseModel):
    id: str
    storyId: str
    title: str
    summary: Optional[str] = None
    category: Optional[str] = None
    trackedAt: str
    latestHeadline: Optional[str] = None
    latestAt: Optional[str] = None
    hasUpdates: bool

async def get_story(story_id: str) -> Dict[str, Any]:
    response = supabase.table('stories').select('id, title, summary, category, geography, status, embedding')\
        .eq('id', story_id).maybe_single().execute()
    story = unwrap_maybe('stories.get', response)
    if not story:
        raise errors.not_found('Story')
    return story

async def tracked_row(user_id: str, story_id: str) -> Optional[Dict[str, Any]]:
    response = supabase.table('tracked_stories').select('id, tracked_at, last_seen_development_id')\
        .eq('user_id', user_id).eq('story_id', story_id).maybe_single().execute()
    return unwrap_maybe('tracked.get', response)

async def get_timeline(user_id: str, story_id: str) -> Dict[str, Any]:
    story = await get_story(story_id)
    
    devs_response = supabase.table('developments')\
        .select('id, headline, summary, what_changed, previous_state, new_state, continuity, occurred_at, world_significance, evidence_strength')\
        .eq('story_id', story_id).order('occurred_at', desc=False).execute()
    rows = unwrap('developments.timeline', devs_response)
    rows = rows if isinstance(rows, list) else [rows]
    
    src_response = supabase.table('story_sources')\
        .select('development_id, url, title, publisher')\
        .eq('story_id', story_id).execute()
    sources = unwrap('story_sources.timeline', src_response)
    sources = sources if isinstance(sources, list) else [sources]
    
    developments: List[TimelineDevelopment] = []
    for index, d in enumerate(rows):
        d_sources = []
        for s in sources:
            if s['development_id'] == d['id']:
                d_sources.append(SourceSummary(
                    name=s.get('publisher') or 'Source',
                    url=s['url'],
                    title=s.get('title')
                ))
                
        continuity_val = d.get('continuity')
        continuity = continuity_val if continuity_val in ('expected', 'unexpected', 'disruptive') else None
        
        developments.append(TimelineDevelopment(
            id=d['id'],
            dayNumber=index + 1,
            headline=d['headline'],
            summary=d.get('summary'),
            whatChanged=d['what_changed'],
            previousState=d['previous_state'],
            newState=d['new_state'],
            continuity=continuity,
            occurredAt=d['occurred_at'],
            worldSignificance=d.get('world_significance'),
            evidenceStrength=d.get('evidence_strength'),
            sources=d_sources
        ))
        
    tracked = await tracked_row(user_id, story_id)
    change_since_last_seen = None
    
    if tracked:
        last_seen = next((d for d in rows if d['id'] == tracked['last_seen_development_id']), None)
        
        if last_seen:
            since_time = datetime.fromisoformat(last_seen['occurred_at'].replace('Z', '+00:00')).timestamp()
            since = last_seen['occurred_at']
        else:
            since_time = datetime.fromisoformat(tracked['tracked_at'].replace('Z', '+00:00')).timestamp()
            since = tracked['tracked_at']
            
        new_devs = [
            d for d in developments 
            if datetime.fromisoformat(d.occurredAt.replace('Z', '+00:00')).timestamp() > since_time
        ]
        change_since_last_seen = {
            "since": since,
            "developments": new_devs
        }
        
    return {
        "story": {
            "id": story['id'],
            "title": story['title'],
            "summary": story.get('summary'),
            "category": story.get('category'),
            "geography": story.get('geography')
        },
        "developments": developments,
        "tracked": tracked is not None,
        "changeSinceLastSeen": change_since_last_seen
    }

async def mark_seen(user_id: str, story_id: str) -> None:
    response = supabase.table('developments').select('id')\
        .eq('story_id', story_id).order('occurred_at', desc=True).limit(1).maybe_single().execute()
    latest = unwrap_maybe('developments.latest', response)
    if not latest:
        return
        
    update_response = supabase.table('tracked_stories').update({
        "last_seen_development_id": latest['id'],
        "updated_at": datetime.utcnow().isoformat() + "Z"
    }).eq('user_id', user_id).eq('story_id', story_id).execute()
    unwrap_void('tracked.markSeen', update_response)

async def track_story(user_id: str, story_id: str, background: bool = False) -> None:
    story = await get_story(story_id)
    
    ambition_id = None
    embedding_str = story.get('embedding')
    if embedding_str:
        # Parse if it comes back as string from Supabase
        embedding = json.loads(embedding_str) if isinstance(embedding_str, str) else embedding_str
        matches = rpc_rows('match_ambitions', {
            "p_user_id": user_id,
            "p_embedding": embedding,
            "p_limit": 1
        })
        if matches and matches[0].get('similarity', 0) >= AMBITION_LINK_MIN:
            ambition_id = matches[0]['id']
            
    dev_res = supabase.table('developments').select('id')\
        .eq('story_id', story_id).order('occurred_at', desc=True).limit(1).maybe_single().execute()
    latest = unwrap_maybe('developments.latest', dev_res)
    
    upsert_data = {
        "user_id": user_id,
        "story_id": story_id,
        "ambition_id": ambition_id,
        "last_seen_development_id": latest['id'] if latest else None,
        "tracked_at": datetime.utcnow().isoformat() + "Z",
        "status": "active",
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    # For python supabase client, upsert doesn't have onConflict directly in python params always, 
    # but we can pass it if supported, or just rely on default primary key conflict resolution.
    # user_id, story_id are likely composite PK.
    upsert_res = supabase.table('tracked_stories').upsert(upsert_data).execute()
    unwrap_void('tracked.upsert', upsert_res)
    
    log.info('story tracked', {"userId": user_id, "storyId": story_id, "ambitionId": ambition_id})
    
    from src.services.briefing import refresh_briefing
    await refresh_briefing(user_id, {"storyId": story_id, "background": background})

async def untrack_story(user_id: str, story_id: str, background: bool = False) -> None:
    await get_story(story_id)
    del_res = supabase.table('tracked_stories').delete().eq('user_id', user_id).eq('story_id', story_id).execute()
    unwrap_void('tracked.delete', del_res)
    log.info('story untracked', {"userId": user_id, "storyId": story_id})
    
    from src.services.briefing import refresh_briefing
    await refresh_briefing(user_id, {"storyId": story_id, "background": background})

async def list_tracked_stories(user_id: str) -> List[TrackedStorySummary]:
    res = supabase.table('tracked_stories')\
        .select('id, story_id, tracked_at, last_seen_development_id, stories(title, summary, category)')\
        .eq('user_id', user_id).order('tracked_at', desc=True).execute()
    rows = unwrap('tracked.list', res)
    rows = rows if isinstance(rows, list) else [rows]
    
    result: List[TrackedStorySummary] = []
    for row in rows:
        dev_res = supabase.table('developments')\
            .select('id, headline, occurred_at')\
            .eq('story_id', row['story_id'])\
            .order('occurred_at', desc=True).limit(1).maybe_single().execute()
        latest = unwrap_maybe('developments.latest', dev_res)
        
        stories_data = row.get('stories') or {}
        
        result.append(TrackedStorySummary(
            id=row['id'],
            storyId=row['story_id'],
            title=stories_data.get('title', ''),
            summary=stories_data.get('summary'),
            category=stories_data.get('category'),
            trackedAt=row['tracked_at'],
            latestHeadline=latest['headline'] if latest else None,
            latestAt=latest['occurred_at'] if latest else None,
            hasUpdates=(latest is not None and latest['id'] != row.get('last_seen_development_id'))
        ))
        
    return result
