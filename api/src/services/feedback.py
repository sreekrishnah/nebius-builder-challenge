from typing import Optional, Literal, List, Dict, Any
from pydantic import BaseModel, Field, root_validator
from datetime import datetime

from src.lib.errors import errors
from src.lib.logger import create_logger
from src.lib.supabase import supabase, unwrap, unwrap_maybe, unwrap_void
from src.services.briefing import refresh_briefing, RefreshOptions
from src.services.memory import days_from_now, remember, MemoryInput
from src.services.stories import get_story, track_story, untrack_story

log = create_logger('feedback')

FeedbackType = Literal[
    'relevant',
    'not_relevant',
    'already_know',
    'too_much',
    'more_like_this',
    'less_like_this',
    'track',
    'untrack',
    'source_not_trusted',
    'not_interested_temporarily'
]

FEEDBACK_TYPES: List[FeedbackType] = [
    'relevant',
    'not_relevant',
    'already_know',
    'too_much',
    'more_like_this',
    'less_like_this',
    'track',
    'untrack',
    'source_not_trusted',
    'not_interested_temporarily'
]

class FeedbackInput(BaseModel):
    storyId: Optional[str] = None
    developmentId: Optional[str] = None
    reportItemId: Optional[str] = None
    feedbackType: FeedbackType
    reason: Optional[str] = Field(None, max_length=500)
    days: Optional[int] = Field(None, ge=1, le=365)
    
    @root_validator(pre=True)
    def check_required_fields(cls, values):
        # Allow missing in pre-validator, but check post-parse
        return values
        
    @root_validator(skip_on_failure=True)
    def validate_rules(cls, values):
        story_id = values.get('storyId')
        development_id = values.get('developmentId')
        report_item_id = values.get('reportItemId')
        
        if not story_id and not development_id and not report_item_id:
            raise ValueError('storyId, developmentId or reportItemId is required')
            
        if values.get('feedbackType') == 'not_interested_temporarily' and values.get('days') is None:
            raise ValueError('days is required for temporary feedback')
            
        return values

class Target(BaseModel):
    storyId: str
    developmentId: Optional[str] = None
    reportItemId: Optional[str] = None

async def resolve_target(user_id: str, input_data: FeedbackInput) -> Target:
    story_id = input_data.storyId
    development_id = input_data.developmentId
    report_item_id = input_data.reportItemId
    
    if report_item_id:
        res = supabase.table('report_items')\
            .select('story_id, development_id, daily_reports!inner(user_id)')\
            .eq('id', report_item_id)\
            .eq('daily_reports.user_id', user_id).maybe_single().execute()
            
        item = unwrap_maybe('report_items.target', res)
        if not item:
            raise errors.not_found('Briefing item')
            
        story_id = story_id or item.get('story_id')
        development_id = development_id or item.get('development_id')
        
    if not story_id and development_id:
        res = supabase.table('developments').select('story_id')\
            .eq('id', development_id).maybe_single().execute()
        dev = unwrap_maybe('developments.target', res)
        if not dev:
            raise errors.not_found('Development')
        story_id = dev.get('story_id')
        
    if not story_id:
        raise errors.validation('Could not determine which story the feedback refers to.')
        
    return Target(storyId=story_id, developmentId=development_id, reportItemId=report_item_id)

async def publishers_of(story_id: str, development_id: Optional[str]) -> List[str]:
    query = supabase.table('story_sources').select('publisher').eq('story_id', story_id)
    if development_id:
        query = query.eq('development_id', development_id)
        
    res = query.execute()
    rows = unwrap('story_sources.publishers', res)
    rows = rows if isinstance(rows, list) else [rows]
    
    pubs = set()
    for r in rows:
        p = r.get('publisher')
        if p:
            pubs.add(p.lower())
    return list(pubs)

async def apply_feedback(user_id: str, input_data: FeedbackInput, background: bool = False) -> Dict[str, str]:
    target = await resolve_target(user_id, input_data)
    story = await get_story(target.storyId)
    feedback_type = input_data.feedbackType
    
    expires_at = days_from_now(input_data.days) if feedback_type == 'not_interested_temporarily' and input_data.days else None
    
    insert_data = {
        "user_id": user_id,
        "story_id": target.storyId,
        "development_id": target.developmentId,
        "report_item_id": target.reportItemId,
        "feedback_type": feedback_type,
        "reason": input_data.reason,
        "expires_at": expires_at.isoformat() + "Z" if expires_at else None
    }
    res = supabase.table('feedback').insert(insert_data).execute()
    unwrap_void('feedback.insert', res)
    
    note = f"{feedback_type}: {input_data.reason}" if input_data.reason else feedback_type
    effect = ''
    
    if feedback_type == 'not_relevant':
        await remember(MemoryInput(userId=user_id, type='suppression', topic=f"story:{story.id}", content=f"Not interested in \"{story.title}\"", source='explicit', confidence=1.0, structured={"storyId": story.id, "note": note}))
        effect = 'story_hidden'
    elif feedback_type == 'already_know':
        if not target.developmentId:
            raise errors.validation('developmentId or reportItemId is required for already_know.')
        await remember(MemoryInput(userId=user_id, type='suppression', topic=f"development:{target.developmentId}", content=f"Already knows an update on \"{story.title}\"", source='explicit', confidence=1.0, structured={"storyId": story.id, "developmentId": target.developmentId}))
        effect = 'update_hidden'
    elif feedback_type == 'too_much':
        await remember(MemoryInput(userId=user_id, type='temporary_suppression', topic=f"story:{story.id}", content=f"Too much coverage of \"{story.title}\"", source='explicit', confidence=1.0, validUntil=days_from_now(7), structured={"storyId": story.id}))
        effect = 'story_paused_7_days'
    elif feedback_type == 'not_interested_temporarily':
        await remember(MemoryInput(userId=user_id, type='temporary_suppression', topic=f"story:{story.id}", content=f"Paused \"{story.title}\"", source='explicit', confidence=1.0, validUntil=expires_at, structured={"storyId": story.id, "days": input_data.days}))
        effect = f"story_paused_{input_data.days}_days"
    elif feedback_type == 'less_like_this':
        await remember(MemoryInput(userId=user_id, type='temporary_suppression', topic=story.title, content=f"Less like \"{story.title}\"", source='explicit', confidence=0.8, validUntil=days_from_now(30), structured={"storyId": story.id}))
        effect = 'similar_stories_reduced_30_days'
    elif feedback_type == 'more_like_this':
        await remember(MemoryInput(userId=user_id, type='interest', topic=story.title, content=f"More like \"{story.title}\"", source='explicit', strength=0.85, confidence=0.9, structured={"storyId": story.id}))
        effect = 'interest_added'
    elif feedback_type == 'relevant':
        await remember(MemoryInput(userId=user_id, type='interest', topic=story.title, content=f"Found \"{story.title}\" relevant", source='explicit', strength=0.65, confidence=0.8, structured={"storyId": story.id}))
        effect = 'interest_added'
    elif feedback_type == 'source_not_trusted':
        publishers = await publishers_of(story.id, target.developmentId)
        if len(publishers) == 0:
            raise errors.validation('No source is recorded for this item.')
        for p in publishers:
            await remember(MemoryInput(userId=user_id, type='suppression', topic=f"source:{p}", content=f"Does not trust {p}", source='explicit', confidence=1.0))
        effect = 'source_hidden'
    elif feedback_type == 'track':
        await track_story(user_id, story.id, background)
        return {"effect": 'story_tracked'}
    elif feedback_type == 'untrack':
        await untrack_story(user_id, story.id, background)
        return {"effect": 'story_untracked'}
        
    await refresh_briefing(user_id, RefreshOptions(storyId=story.id, background=background))
    log.info('feedback applied', {"userId": user_id, "storyId": story.id, "type": feedback_type, "effect": effect})
    return {"effect": effect}
