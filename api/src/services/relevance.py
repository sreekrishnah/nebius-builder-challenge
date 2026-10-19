import json
from typing import List, Optional, Dict, Any, Set
from pydantic import BaseModel
from datetime import datetime

from src.ai.tasks import assess_relevance
from src.domain.attention import AssumptionImpact
from src.domain.relevance import AmbitionAssessment, AmbitionCandidate, DevelopmentFacts, RelevanceDecision, decide_relevance, RelevanceInput
from src.domain.types import MemoryRow
from src.lib.logger import create_logger
from src.lib.supabase import supabase, unwrap, unwrap_maybe, unwrap_void, rpc_rows
from src.services.assumptions import AssumptionRow, assumptions_key, list_assumptions, mark_challenged
from src.services.memory import get_active_memory, get_identity, retrieve_similar_memory

log = create_logger('relevance')

class AmbitionRecord(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    horizon: Optional[str] = None
    geography: Optional[str] = None
    priority: int
    updated_at: str

class DevelopmentRecord(BaseModel):
    id: str
    story_id: str
    headline: str
    summary: Optional[str] = None
    what_changed: str
    continuity: Optional[str] = None
    occurred_at: str
    world_significance: Optional[float] = None
    significance_reason: Optional[str] = None
    embedding: Optional[str] = None
    stories: Optional[Dict[str, Any]] = None
    story_sources: Optional[List[Dict[str, Any]]] = None

class CachedAssessment(AmbitionAssessment):
    language: Optional[str] = None
    ambitionId: str
    ambitionUpdatedAt: str
    assumptionsKey: Optional[str] = None
    interestsKey: Optional[str] = None

class UserRelevanceContext(BaseModel):
    userId: str
    memory: List[MemoryRow]
    identity: Dict[str, str]
    reportLanguage: str
    ambitions: Dict[str, AmbitionRecord]
    trackedStoryIds: Set[str]
    assumptions: Dict[str, List[AssumptionRow]]
    interests: List[str]

class EvaluatedDevelopment(BaseModel):
    developmentId: str
    storyId: str
    decision: RelevanceDecision
    ambitionTitle: Optional[str] = None

DEVELOPMENT_SELECT = 'id, story_id, headline, summary, what_changed, continuity, occurred_at, world_significance, significance_reason, embedding, stories(id, title, category, geography, entities_json), story_sources(publisher)'

def explicit_interests(memory: List[MemoryRow]) -> List[str]:
    import re
    res = []
    for m in memory:
        if m.memory_type in ('interest', 'temporary_interest') and m.source != 'inferred':
            t = (m.topic or '').strip()
            if len(t) > 1 and not re.match(r'^(story|development|source):', t):
                if t not in res:
                    res.append(t)
    return res[:12]

async def load_user_context(user_id: str) -> UserRelevanceContext:
    import asyncio
    
    t_mem = get_active_memory(user_id)
    t_id = get_identity(user_id)
    
    prof_res = supabase.table('user_intelligence_profiles').select('report_language').eq('user_id', user_id).maybe_single().execute()
    amb_res = supabase.table('ambitions').select('id, title, description, horizon, geography, priority, updated_at')\
        .eq('user_id', user_id).eq('status', 'active').execute()
    trk_res = supabase.table('tracked_stories').select('story_id').eq('user_id', user_id).execute()
    
    memory, identity, assumption_rows = await asyncio.gather(t_mem, t_id, t_ass)
    
    prof_row = unwrap_maybe('profile.language', prof_res)
    amb_rows = unwrap('ambitions.load', amb_res)
    amb_rows = amb_rows if isinstance(amb_rows, list) else [amb_rows]
    trk_rows = unwrap('tracked.load', trk_res)
    trk_rows = trk_rows if isinstance(trk_rows, list) else [trk_rows]
    
    assumptions: Dict[str, List[AssumptionRow]] = {}
    for r in assumption_rows:
        if r.ambition_id not in assumptions:
            assumptions[r.ambition_id] = []
        assumptions[r.ambition_id].append(r)
        
    lang = prof_row.get('report_language', '').strip() if prof_row else ''
    if not lang:
        lang = 'English'
        
    ambs = {a['id']: AmbitionRecord(**a) for a in amb_rows}
    trk_set = set([t['story_id'] for t in trk_rows])
    
    return UserRelevanceContext(
        userId=user_id,
        memory=memory,
        identity=identity,
        reportLanguage=lang,
        ambitions=ambs,
        trackedStoryIds=trk_set,
        assumptions=assumptions,
        interests=explicit_interests(memory)
    )

def to_facts(dev: DevelopmentRecord) -> DevelopmentFacts:
    story = dev.stories or {}
    ej = story.get('entities_json')
    entities = ej if isinstance(ej, list) else []
    
    pubs = [s.get('publisher', '') for s in (dev.story_sources or []) if s.get('publisher')]
    
    return DevelopmentFacts(
        developmentId=dev.id,
        storyId=dev.story_id,
        storyTitle=story.get('title', ''),
        geography=story.get('geography'),
        entities=[e for e in entities if isinstance(e, str)],
        text=f"{dev.headline} {dev.what_changed}",
        worldSignificance=dev.world_significance or 0,
        occurredAt=dev.occurred_at,
        publishers=pubs
    )

async def load_cached_assessment(user_id: str, development_id: str) -> Optional[CachedAssessment]:
    response = supabase.table('world_coverage').select('relevance_reasons')\
        .eq('user_id', user_id).eq('development_id', development_id).maybe_single().execute()
    row = unwrap_maybe('coverage.cache', response)
    if not row or not row.get('relevance_reasons'):
        return None
    assessment = row['relevance_reasons'].get('assessment')
    if assessment:
        return CachedAssessment(**assessment)
    return None

async def run_assessment(ctx: UserRelevanceContext, dev: DevelopmentRecord, ambition: AmbitionRecord, assumptions: List[AssumptionRow]) -> CachedAssessment:
    res = supabase.table('developments').select('headline, occurred_at')\
        .eq('story_id', dev.story_id).neq('id', dev.id)\
        .order('occurred_at', desc=True).limit(3).execute()
    hist_rows = unwrap('developments.history', res)
    hist_rows = hist_rows if isinstance(hist_rows, list) else [hist_rows]
    
    history = [{"occurredAt": h['occurred_at'], "headline": h['headline']} for h in hist_rows]
    
    result = await assess_relevance(
        ambition={"title": ambition.title, "description": ambition.description, "horizon": ambition.horizon, "geography": ambition.geography},
        identity=ctx.identity,
        story={"title": dev.stories.get('title', '') if dev.stories else '', "category": dev.stories.get('category') if dev.stories else None, "geography": dev.stories.get('geography') if dev.stories else None},
        development={"headline": dev.headline, "whatChanged": dev.what_changed, "continuity": dev.continuity, "occurredAt": dev.occurred_at},
        recentHistory=history,
        assumptions=[{"id": a.id, "statement": a.statement} for a in assumptions],
        interests=ctx.interests,
        language=ctx.reportLanguage
    )
    
    impact = result.assumption_impact
    known = any(a.id == impact.assumption_id for a in assumptions)
    assumption_impact = None
    if impact.effect != 'none' and impact.assumption_id and known and impact.reason.strip():
        assumption_impact = AssumptionImpact(assumptionId=impact.assumption_id, effect=impact.effect, reason=impact.reason.strip())
        
    return CachedAssessment(
        ambitionId=ambition.id,
        ambitionUpdatedAt=ambition.updated_at,
        relation=result.relation,
        confidence=result.confidence,
        whyItMatters=result.why_it_matters.strip(),
        couldChange=result.could_change.strip(),
        assumptionImpact=assumption_impact,
        assumptionsKey=assumptions_key([{"id": a.id, "statement": a.statement} for a in assumptions]),
        interestsKey="|".join(ctx.interests),
        language=ctx.reportLanguage
    )

import asyncio

async def evaluate_development(ctx: UserRelevanceContext, dev: DevelopmentRecord) -> EvaluatedDevelopment:
    facts = to_facts(dev)
    import re
    has_semantic_memory = any(m.topic and not re.match(r'^(story|development|source):', m.topic) for m in ctx.memory)
    
    t_sim = retrieve_similar_memory(ctx.userId, json.loads(dev.embedding), 50) if has_semantic_memory and dev.embedding else asyncio.sleep(0, result=[])
    
    similar = await t_sim
    matches = rpc_rows('match_ambitions', {"p_user_id": ctx.userId, "p_embedding": json.loads(dev.embedding), "p_limit": 1}) if dev.embedding and len(ctx.ambitions) > 0 else []
    
    memory_similarity = {m.id: getattr(m, 'similarity', 0.0) for m in similar}
    candidate = None
    if matches and len(matches) > 0:
        candidate = AmbitionCandidate(id=matches[0]['id'], title=matches[0]['title'], priority=matches[0]['priority'], similarity=matches[0]['similarity'])
        
    ambition = ctx.ambitions.get(candidate.id) if candidate else None
    
    base_input = RelevanceInput(
        development=facts,
        memory=ctx.memory,
        memorySimilarity=memory_similarity,
        trackedStory=dev.story_id in ctx.trackedStoryIds,
        ambition=candidate,
        assessment=None,
        identity=ctx.identity,
        now=datetime.utcnow()
    )
    
    ambition_assumptions = ctx.assumptions.get(ambition.id, []) if ambition else []
    assessment = None
    decision = decide_relevance(base_input)
    
    if decision.needsAmbitionAssessment and ambition:
        cached = await load_cached_assessment(ctx.userId, dev.id)
        usable = (cached and cached.ambitionId == ambition.id and cached.ambitionUpdatedAt == ambition.updated_at 
                  and isinstance(cached.couldChange, str) and cached.language == ctx.reportLanguage 
                  and cached.assumptionsKey == assumptions_key([{"id": a.id, "statement": a.statement} for a in ambition_assumptions]) 
                  and cached.interestsKey == "|".join(ctx.interests))
                  
        assessment = cached if usable else None
        if not assessment:
            assessment = await run_assessment(ctx, dev, ambition, ambition_assumptions)
            
        recorded = next((a for a in ambition_assumptions if a.id == (assessment.assumptionImpact.assumptionId if assessment.assumptionImpact else None)), None)
        
        # Only an assumption recorded before the development happened can be challenged by it
        from src.services.assumptions import AssumptionRow
        if recorded:
            dt_recorded = datetime.fromisoformat(recorded.updated_at.replace('Z', '+00:00'))
            dt_dev = datetime.fromisoformat(dev.occurred_at.replace('Z', '+00:00'))
            if dt_recorded.timestamp() >= dt_dev.timestamp():
                assessment.assumptionImpact = None
                
        # Re-evaluate with assessment
        base_input.assessment = assessment
        decision = decide_relevance(base_input)
        
        if decision.assumption and decision.assumption.effect == 'challenges':
            await mark_challenged(decision.assumption.assumptionId, dev.id, decision.assumption.reason)
            
    factors_dump = [f.dict() for f in decision.factors]
    assessment_dump = assessment.dict() if assessment else None
    
    upsert_data = {
        "user_id": ctx.userId,
        "development_id": dev.id,
        "world_significance": dev.world_significance,
        "significance_reason": dev.significance_reason,
        "personal_relevance": decision.personalRelevance,
        "relevance_reasons": {
            "version": 1,
            "factors": factors_dump,
            "assessment": assessment_dump,
            "suppressedMemoryId": decision.suppressedMemoryId
        },
        "relevance_basis": decision.basis,
        "ambition_id": decision.ambitionId,
        "suppressed": decision.suppressed,
        "suppressed_reason": decision.suppressedReason,
        "shown_to_user": decision.show,
        "attention": decision.attention,
        "must_know": False,
        "suppression_allowed": True,
        "evaluated_at": datetime.utcnow().isoformat() + "Z"
    }
    
    res = supabase.table('world_coverage').upsert(upsert_data).execute()
    unwrap_void('coverage.upsert', res)
    
    log.debug('development evaluated', {"userId": ctx.userId, "developmentId": dev.id, "show": decision.show, "basis": decision.basis, "score": decision.personalRelevance})
    
    return EvaluatedDevelopment(
        developmentId=dev.id,
        storyId=dev.story_id,
        decision=decision,
        ambitionTitle=ctx.ambitions[decision.ambitionId].title if decision.ambitionId and decision.ambitionId in ctx.ambitions else None
    )

async def load_developments(since: Optional[datetime] = None, ids: Optional[List[str]] = None, storyId: Optional[str] = None, limit: int = 100) -> List[DevelopmentRecord]:
    query = supabase.table('developments').select(DEVELOPMENT_SELECT).order('occurred_at', desc=True).limit(limit)
    if since:
        query = query.gte('occurred_at', since.isoformat() + 'Z')
    if ids:
        query = query.in_('id', ids)
    if storyId:
        query = query.eq('story_id', storyId)
        
    res = query.execute()
    rows = unwrap('developments.load', res)
    rows = rows if isinstance(rows, list) else [rows]
    return [DevelopmentRecord(**r) for r in rows]

async def evaluate_many(ctx: UserRelevanceContext, devs: List[DevelopmentRecord], on_progress=None) -> Dict[str, Any]:
    evaluated = []
    failed = 0
    done = 0
    
    # Process sequentially for simplicity and to match typescript's small concurrency
    for dev in devs:
        try:
            ev = await evaluate_development(ctx, dev)
            evaluated.append(ev)
        except Exception as err:
            failed += 1
            log.error('development evaluation failed', {"userId": ctx.userId, "developmentId": dev.id, "err": str(err)})
        done += 1
        if on_progress:
            on_progress(done, len(devs))
            
    return {"evaluated": evaluated, "failed": failed}
