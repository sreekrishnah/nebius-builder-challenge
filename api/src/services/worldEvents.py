import hashlib
import json
from typing import List, Dict, Any, Optional
from pydantic import BaseModel

from src.ai.tasks import assess_must_know_events
from src.ai.guard import clean_model_text
from src.domain.relevance import THRESHOLDS, RelevanceDecision, RelevanceFactor
from src.lib.logger import create_logger
from src.lib.supabase import supabase, unwrap, unwrap_void
from src.services.relevance import DevelopmentRecord, EvaluatedDevelopment, UserRelevanceContext

log = create_logger('world-events')

MAX_CANDIDATES = 8
MAX_PICKS = 3
WORLD_EVENT_SCORE = 0.6

class StoredWorldEvent(BaseModel):
    key: str
    mustKnow: bool
    why: str
    couldChange: str

class CoverageRow(BaseModel):
    development_id: str
    relevance_reasons: Optional[Dict[str, Any]] = None

def profile_key(ctx: UserRelevanceContext) -> str:
    # ambitions is a map of id -> AmbitionRecord
    ambs = sorted([f"{a.id}:{a.updated_at}" for a in ctx.ambitions.values()])
    parts = [ctx.reportLanguage, json.dumps(ctx.identity)] + ambs
    
    hash_str = "|".join(parts)
    return hashlib.sha1(hash_str.encode('utf-8')).hexdigest()[:16]

def stored(row: Optional[CoverageRow], key: str) -> Optional[StoredWorldEvent]:
    if not row or not row.relevance_reasons:
        return None
    value = row.relevance_reasons.get('worldEvent')
    if value and isinstance(value, dict) and value.get('key') == key:
        return StoredWorldEvent(**value)
    return None

async def pick_must_know_events(ctx: UserRelevanceContext, devs: List[DevelopmentRecord], evaluated: List[EvaluatedDevelopment]) -> List[EvaluatedDevelopment]:
    handled = {e.developmentId for e in evaluated if e.decision.show or e.decision.suppressed}
    
    candidates = []
    for d in devs:
        ws = d.world_significance or 0.0
        if ws >= THRESHOLDS.generalSignificance and d.id not in handled:
            candidates.append(d)
            
    candidates.sort(key=lambda d: d.world_significance or 0.0, reverse=True)
    candidates = candidates[:MAX_CANDIDATES]
    
    if len(candidates) == 0 or len(ctx.ambitions) == 0:
        return []
        
    key = profile_key(ctx)
    res = supabase.table('world_coverage').select('development_id, relevance_reasons')\
        .eq('user_id', ctx.userId)\
        .in_('development_id', [c.id for c in candidates]).execute()
        
    rows_data = unwrap('world.coverage', res)
    rows_data = rows_data if isinstance(rows_data, list) else [rows_data]
    rows = [CoverageRow(**r) for r in rows_data]
    
    row_by_id = {r.development_id: r for r in rows}
    results: Dict[str, StoredWorldEvent] = {}
    to_ask: List[DevelopmentRecord] = []
    
    for dev in candidates:
        cached = stored(row_by_id.get(dev.id), key)
        if cached:
            results[dev.id] = cached
        else:
            to_ask.append(dev)
            
    if len(to_ask) > 0:
        verdicts = await assess_must_know_events(
            ambitions=[{"title": a.title, "description": a.description, "horizon": a.horizon, "geography": a.geography} for a in ctx.ambitions.values()],
            identity=ctx.identity,
            events=[{"headline": d.headline, "whatChanged": d.what_changed, "why": d.significance_reason} for d in to_ask],
            language=ctx.reportLanguage
        )
        
        for verdict in verdicts:
            idx = verdict.index - 1
            if idx < 0 or idx >= len(to_ask):
                continue
            dev = to_ask[idx]
            why = clean_model_text(verdict.why_it_matters, 700)
            must_know = verdict.must_know and len(why) > 0
            results[dev.id] = StoredWorldEvent(
                key=key, 
                mustKnow=must_know, 
                why=why, 
                couldChange=clean_model_text(verdict.could_change, 300)
            )
            
        for dev in to_ask:
            if dev.id not in results:
                results[dev.id] = StoredWorldEvent(key=key, mustKnow=False, why='', couldChange='')
                
    picks = [d for d in candidates if results[d.id].mustKnow][:MAX_PICKS]
    picked_ids = {d.id for d in picks}
    
    for dev in candidates:
        result = results[dev.id]
        picked = dev.id in picked_ids
        r_row = row_by_id.get(dev.id)
        previous = r_row.relevance_reasons if r_row and r_row.relevance_reasons else {}
        
        upd_data = {
            "relevance_reasons": {**previous, "worldEvent": result.dict()},
            "shown_to_user": picked,
            "relevance_basis": 'general' if picked else None,
            "attention": 'know' if picked else None,
            "personal_relevance": WORLD_EVENT_SCORE if picked else 0,
            "override_triggered": picked,
            "override_type": 'must_know_world_event' if picked else None
        }
        
        upd_res = supabase.table('world_coverage').update(upd_data)\
            .eq('user_id', ctx.userId).eq('development_id', dev.id).execute()
        unwrap_void('world.store', upd_res)
        
    log.info('must-know world events', {"userId": ctx.userId, "candidates": len(candidates), "picked": len(picks)})
    
    ret = []
    for dev in picks:
        result = results[dev.id]
        
        factor = RelevanceFactor(rule=7, basis='general', effect='boost', detail='An unavoidable world event with a stated effect on you')
        
        decision = RelevanceDecision(
            show=True,
            suppressed=False,
            suppressedReason=None,
            suppressedMemoryId=None,
            basis='general',
            personalRelevance=WORLD_EVENT_SCORE,
            factors=[factor],
            ambitionId=None,
            whyItMatters=result.why,
            couldChange=result.couldChange or None,
            attention='know',
            assumption=None,
            needsAmbitionAssessment=False
        )
        
        ret.append(EvaluatedDevelopment(
            developmentId=dev.id,
            storyId=dev.story_id,
            ambitionTitle=None,
            decision=decision
        ))
        
    return ret
