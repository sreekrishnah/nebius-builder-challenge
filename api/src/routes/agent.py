import json
import re
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, Path

from src.lib.errors import errors
from src.lib.supabase import supabase, unwrap, unwrap_maybe
from src.http.middleware import get_current_user
from src.services.briefing import get_report_for_date, get_report_items, list_report_days
from src.services.script import get_latest_script, get_script_for_report
from src.services.voiceUsage import get_voice_usage

router = APIRouter()

PRIORITY_LABEL = {"high": 'High Priority', "medium": 'Medium Priority', "next": 'Next Step'}

def to_session(row):
    if not isinstance(row, dict) or not row.get('id'):
        return None
    summary = row.get('summary') or {}
    if isinstance(summary, str):
        try:
            summary = json.loads(summary)
        except Exception:
            summary = {}
    if not isinstance(summary, dict):
        summary = {}

    next_steps = []
    for i, s in enumerate(summary.get('nextSteps', [])):
        if isinstance(s, dict):
            next_steps.append({
                "id": f"{row.get('id', '')}-{i}",
                "title": s.get('title'),
                "description": s.get('description'),
                "priority": PRIORITY_LABEL.get(s.get('priority', 'next'), 'Next Step')
            })
        elif isinstance(s, str):
            next_steps.append({
                "id": f"{row.get('id', '')}-{i}",
                "title": s,
                "description": None,
                "priority": 'Next Step'
            })

    return {
        "id": row.get('id', ''),
        "endedAt": row.get('ended_at'),
        "summary": summary.get('summary', ''),
        "keyPoints": summary.get('keyPoints', []),
        "sources": summary.get('sources', []),
        "nextSteps": next_steps
    }

def serialize_item(item):
    if hasattr(item, 'model_dump'):
        return item.model_dump()
    if hasattr(item, 'dict'):
        return item.dict()
    return item

@router.get('/summary', include_in_schema=False)
@router.get('/summary/')
async def get_summary(user_id: str = Depends(get_current_user)):
    res = supabase.table('bidi_sessions').select('id, ended_at, summary').eq('user_id', user_id).eq('status', 'ended').not_.is_('summary', 'null').order('ended_at', desc=True).limit(1).maybe_single().execute()
    row = unwrap_maybe('bidi_sessions.latest', res)
    if not row:
        return {"success": True, "session": None}
    return {"success": True, "session": to_session(row)}

@router.get('/days', include_in_schema=False)
@router.get('/days/')
async def get_days(user_id: str = Depends(get_current_user)):
    days = await list_report_days(user_id)
    return {"success": True, "days": days}

DATE_PARAM = re.compile(r'^\d{4}-\d{2}-\d{2}$')

@router.get('/days/{date}', include_in_schema=False)
@router.get('/days/{date}/')
async def get_day_info(date: str = Path(...), user_id: str = Depends(get_current_user)):
    if not DATE_PARAM.match(date):
        raise errors.validation('Choose a valid date.')
        
    try:
        # Standard library ISO 8601 parsing with IST offset
        day_start = datetime.fromisoformat(f"{date}T00:00:00+05:30")
    except Exception:
        raise errors.validation('Choose a valid date.')
        
    report = await get_report_for_date(user_id, date)
    
    if report:
        import asyncio
        items, script = await asyncio.gather(
            get_report_items(user_id, report, 50),
            get_script_for_report(user_id, report['id'])
        )
    else:
        items, script = [], None
    
    day_end = day_start + timedelta(days=1)
    
    calls_res = supabase.table('bidi_sessions').select('id, ended_at, summary').eq('user_id', user_id).eq('status', 'ended').not_.is_('summary', 'null').gte('ended_at', day_start.isoformat()).lt('ended_at', day_end.isoformat()).order('ended_at').execute()
    
    calls = unwrap_maybe('bidi_sessions.day', calls_res) or []
    calls = calls if isinstance(calls, list) else [calls]
    
    sessions = [s for s in (to_session(row) for row in calls) if s is not None]
    
    return {
        "success": True,
        "day": {
            "date": date,
            "generatedAt": report.get('generatedAt') if report else None,
            "items": [serialize_item(i) for i in items] if isinstance(items, list) else [],
            "closing": getattr(script, 'closing', None) if script else (script.get('closing') if isinstance(script, dict) else None),
            "sessions": sessions
        }
    }

@router.get('/usage', include_in_schema=False)
@router.get('/usage/')
async def get_usage(user_id: str = Depends(get_current_user)):
    usage = await get_voice_usage(user_id)
    return {"success": True, "usage": usage.model_dump() if hasattr(usage, 'model_dump') else usage.dict()}

@router.get('/script', include_in_schema=False)
@router.get('/script/')
async def get_script(user_id: str = Depends(get_current_user)):
    stored = await get_latest_script(user_id)
    if not stored:
        return {"success": True, "script": None}
    script_data = stored.script.model_dump() if hasattr(stored.script, 'model_dump') else stored.script.dict()
    return {"success": True, "script": script_data}

