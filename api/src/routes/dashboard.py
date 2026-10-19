import asyncio
from fastapi import APIRouter, Depends

from src.lib.supabase import supabase, unwrap_maybe
from src.services.ambitions import list_ambitions
from src.services.briefing import get_briefing_items
from src.services.lens import get_lens
from src.services.pipeline import get_latest_job
from src.services.stories import list_tracked_stories
from src.http.middleware import get_current_user

router = APIRouter()

@router.get('', include_in_schema=False)
@router.get('/')
async def dashboard(user_id: str = Depends(get_current_user)):
    user_res = supabase.table('users').select('full_name').eq('id', user_id).maybe_single().execute()
    
    ambitions, briefing, tracked_stories, job = await asyncio.gather(
        list_ambitions(user_id),
        get_briefing_items(user_id),
        list_tracked_stories(user_id),
        get_latest_job(user_id)
    )
    
    user_row = unwrap_maybe('users.name', user_res)
    has_active = any(a.status == 'active' for a in ambitions)
    
    if not has_active:
        state = 'no_ambition'
    elif not briefing.get('items'):
        state = 'no_developments'
    else:
        state = 'ready'
        
    return {
        "success": True,
        "state": state,
        "user": {"fullName": user_row.get('full_name') if user_row else None},
        "ambitions": [{
            "id": a.id,
            "title": a.title,
            "description": a.description,
            "horizon": a.horizon,
            "geography": a.geography,
            "priority": a.priority,
            "status": a.status,
            "expiresAt": getattr(a, 'expires_at', None),
            "createdAt": getattr(a, 'created_at', None)
        } for a in ambitions],
        "briefing": {
            "reportId": briefing.get('report', {}).get('id') if briefing.get('report') else None,
            "date": briefing.get('report', {}).get('reportDate') if briefing.get('report') else None,
            "generatedAt": briefing.get('report', {}).get('generatedAt') if briefing.get('report') else None,
            "executiveSummary": briefing.get('report', {}).get('executiveSummary') if briefing.get('report') else None,
            "trajectoryAssessment": briefing.get('report', {}).get('trajectoryAssessment') if briefing.get('report') else None,
            "historicalConnections": briefing.get('report', {}).get('historicalConnections') if briefing.get('report') else None,
            "opportunities": briefing.get('report', {}).get('opportunities') if briefing.get('report') else None,
            "risks": briefing.get('report', {}).get('risks') if briefing.get('report') else None,
            "recommendedActions": briefing.get('report', {}).get('recommendedActions') if briefing.get('report') else None,
            "sources": briefing.get('report', {}).get('sources') if briefing.get('report') else None,
            "uncertainties": briefing.get('report', {}).get('uncertainties') if briefing.get('report') else None,
            "researchPlan": briefing.get('report', {}).get('researchPlan') if briefing.get('report') else None,
            "activityTrace": briefing.get('report', {}).get('activityTrace') if briefing.get('report') else None,
            "modelUsed": briefing.get('report', {}).get('modelUsed') if briefing.get('report') else None,
        },
        "items": [i.dict() for i in briefing.get('items', [])],
        "trackedStories": [t.dict() for t in tracked_stories],
        "pipeline": {
            "status": job.status if job else 'IDLE',
            "lastRunAt": job.completedAt if job else None,
            "error": job.error if job else None,
            "progress": job.progress if job and job.status == 'PROCESSING' else None
        }
    }

@router.get('/lens')
async def get_lens_route(user_id: str = Depends(get_current_user)):
    lens = await get_lens(user_id)
    return {"success": True, "lens": lens.dict()}
