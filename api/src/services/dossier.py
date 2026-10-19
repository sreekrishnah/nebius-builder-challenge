from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from src.lib.supabase import supabase, unwrap

DOSSIER_ITEMS = 8
HISTORY_PER_STORY = 3

class HistoryEntry(BaseModel):
    occurredAt: str
    headline: str
    whatChanged: str
    continuity: Optional[str] = None

class BriefingItemMinimal(BaseModel):
    # This is a minimal definition of BriefingItem from briefing.ts, 
    # to avoid circular imports. We just need storyId and developmentId here.
    storyId: str
    developmentId: str

async def load_story_history(items: List[Any]) -> Dict[str, List[HistoryEntry]]:
    # Extract unique story ids
    story_ids = list(set([i.get('storyId') if isinstance(i, dict) else getattr(i, 'storyId') for i in items]))
    if not story_ids:
        return {}
        
    response = supabase.table('developments')\
        .select('id, story_id, headline, what_changed, continuity, occurred_at')\
        .in_('story_id', story_ids)\
        .order('occurred_at', desc=True)\
        .limit(len(story_ids) * (HISTORY_PER_STORY + 3))\
        .execute()
        
    rows = unwrap('developments.history', response)
    rows = rows if isinstance(rows, list) else [rows]
    
    chapter_dev_ids = set([i.get('developmentId') if isinstance(i, dict) else getattr(i, 'developmentId') for i in items])
    history: Dict[str, List[HistoryEntry]] = {}
    
    for row in rows:
        if row['id'] in chapter_dev_ids:
            continue
            
        story_id = row['story_id']
        if story_id not in history:
            history[story_id] = []
            
        if len(history[story_id]) >= HISTORY_PER_STORY:
            continue
            
        history[story_id].append(HistoryEntry(
            occurredAt=row['occurred_at'],
            headline=row['headline'],
            whatChanged=row['what_changed'],
            continuity=row.get('continuity')
        ))
        
    return history
