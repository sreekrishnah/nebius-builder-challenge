import asyncio
from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel
from datetime import datetime

from src.ai.tasks import ScriptListener, write_chapter_script, write_closing_script
from src.ai.guard import clean_model_text
from src.lib.logger import create_logger
from src.lib.supabase import supabase, unwrap, unwrap_maybe, unwrap_void
from src.services.ambitions import get_profile, list_ambitions
from src.services.briefing import get_briefing_items
from src.services.dossier import DOSSIER_ITEMS, load_story_history
from src.services.briefingDriver import part_seconds

log = create_logger('script')

WORDS_PER_SECOND = 3
MAX_PART_CHARS = 1800

class ChapterScript(BaseModel):
    developmentId: str
    storyId: str
    title: str
    kind: Literal['ambition', 'world']
    attention: str
    facts: str
    sides: str
    forYou: str

class BriefingScript(BaseModel):
    version: Literal[1] = 1
    language: str
    generatedAt: str
    chapters: List[ChapterScript]
    closing: str

class StoredScript(BaseModel):
    reportId: str
    script: BriefingScript

async def load_listener(user_id: str) -> Dict[str, Any]:
    user_row_res = supabase.table('users').select('full_name').eq('id', user_id).maybe_single().execute()
    user_row = unwrap_maybe('script.user', user_row_res)
    
    t_profile = get_profile(user_id)
    t_ambs = list_ambitions(user_id)
    
    profile, ambitions = await asyncio.gather(t_profile, t_ambs)
    
    full_name = user_row.get('full_name') if user_row else None
    name = 'there'
    if full_name and full_name.strip():
        name = full_name.strip().split()[0]
        
    active_ambs = []
    for a in ambitions:
        if a.status == 'active':
            active_ambs.append({
                "title": a.title,
                "description": a.description,
                "horizon": a.horizon,
                "geography": a.geography
            })
    
    return {
        "listener": ScriptListener(
            name=name,
            role=profile.role if profile else None,
            activity=profile.activity if profile else None,
            geography=profile.geography_focus if profile else None,
            ambitions=active_ambs[:3]
        ),
        "language": profile.report_language if (profile and hasattr(profile, 'report_language') and profile.report_language) else (profile.prefered_language if profile and hasattr(profile, 'prefered_language') else 'English'),
        "depth": profile.depth if profile else None
    }

async def generate_briefing_script(user_id: str) -> Optional[BriefingScript]:
    items_res = await get_briefing_items(user_id, DOSSIER_ITEMS)
    report = items_res.get('report')
    items = items_res.get('items', [])
    
    if not report or len(items) == 0:
        return None
        
    listener_data, history = await asyncio.gather(load_listener(user_id), load_story_history(items))
    listener = listener_data['listener']
    language = listener_data['language']
    depth = listener_data['depth']
    
    words_per_part = round(part_seconds(depth) * WORDS_PER_SECOND)
    
    chapters = []
    for index, item in enumerate(items):
        try:
            kind = 'world' if item.relevanceBasis == 'general' else 'ambition'
            
            outlets = []
            seen = set()
            for s in item.sources:
                if s.name and s.name not in seen:
                    seen.add(s.name)
                    outlets.append(s.name)
                    if len(outlets) >= 5:
                        break
                        
            hist = []
            for h in history.get(item.storyId, []):
                hist.append({
                    "date": h.occurredAt[:10],
                    "headline": h.headline,
                    "whatChanged": h.whatChanged
                })
                
            ass_dict = None
            if item.assumption:
                ass_dict = {"statement": item.assumption.statement, "note": item.assumption.note}
                
            next_title = items[index + 1].storyTitle if index + 1 < len(items) else None
            
            parts = await write_chapter_script(
                listener=listener,
                language=language,
                wordsPerPart=words_per_part,
                kind=kind,
                headline=item.headline,
                summary=item.summary,
                whatChanged=item.whatChanged,
                continuity=item.continuity,
                date=item.occurredAt[:10],
                outlets=outlets,
                history=hist,
                why=item.whyItMatters,
                couldChange=item.couldChange,
                assumption=ass_dict,
                nextTitle=next_title
            )
            
            facts = clean_model_text(parts.facts, MAX_PART_CHARS)
            sides = clean_model_text(parts.sides, MAX_PART_CHARS)
            for_you = clean_model_text(parts.for_you, MAX_PART_CHARS)
            
            if not facts or not sides or not for_you:
                raise Exception('script part rejected by the output filter')
                
            chapters.append(ChapterScript(
                developmentId=item.developmentId,
                storyId=item.storyId,
                title=item.storyTitle,
                kind=kind,
                attention=item.attention,
                facts=facts,
                sides=sides,
                forYou=for_you
            ))
        except Exception as err:
            log.warn('story script skipped', {"userId": user_id, "developmentId": item.developmentId, "err": str(err)})
            
    if len(chapters) == 0:
        return None
        
    closing = ""
    try:
        stories_for_closing = []
        for c in chapters:
            why = None
            for it in items:
                if it.developmentId == c.developmentId:
                    why = it.whyItMatters
                    break
            stories_for_closing.append({"title": c.title, "why": why})
            
        raw_closing = await write_closing_script(listener=listener, language=language, stories=stories_for_closing)
        closing = clean_model_text(raw_closing, MAX_PART_CHARS)
    except Exception as err:
        log.warn('closing script skipped', {"userId": user_id, "err": str(err)})
        
    script = BriefingScript(
        version=1,
        language=language,
        generatedAt=datetime.utcnow().isoformat() + "Z",
        chapters=chapters,
        closing=closing
    )
    
    res = supabase.table('briefing_scripts').upsert({
        "user_id": user_id,
        "report_id": report['id'],
        "script": script.dict(),
        "generated_at": script.generatedAt
    }).execute()
    unwrap_void('script.store', res)
    
    log.info('briefing script generated', {"userId": user_id, "reportId": report['id'], "chapters": len(chapters)})
    return script

async def get_script_for_report(user_id: str, report_id: str) -> Optional[BriefingScript]:
    res = supabase.table('briefing_scripts').select('script')\
        .eq('user_id', user_id).eq('report_id', report_id).maybe_single().execute()
        
    row = unwrap_maybe('script.byReport', res)
    if row and row.get('script') and row['script'].get('version') == 1:
        return BriefingScript(**row['script'])
    return None

async def get_latest_script(user_id: str) -> Optional[StoredScript]:
    res = supabase.table('briefing_scripts').select('report_id, script, generated_at')\
        .eq('user_id', user_id).order('generated_at', desc=True).limit(1).execute()
        
    rows = unwrap('script.latest', res)
    rows = rows if isinstance(rows, list) else [rows]
    
    if len(rows) > 0:
        row = rows[0]
        if row.get('script') and row['script'].get('version') == 1:
            return StoredScript(reportId=row['report_id'], script=BriefingScript(**row['script']))
            
    return None
