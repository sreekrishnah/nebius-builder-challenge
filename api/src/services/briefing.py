import asyncio
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from datetime import datetime, timedelta

from src.domain.attention import ATTENTION_RANK
from src.domain.types import Attention, Continuity, RelevanceBasis
from src.lib.logger import create_logger
from src.lib.supabase import supabase, unwrap, unwrap_maybe, unwrap_void
from src.services.relevance import DevelopmentRecord, EvaluatedDevelopment, evaluate_many, load_developments, load_user_context
from src.services.worldEvents import pick_must_know_events

log = create_logger('briefing')

WINDOW_DAYS = 14

class BriefingSource(BaseModel):
    name: str
    url: str
    title: Optional[str] = None
    publishedAt: Optional[str] = None

class AssumptionNote(BaseModel):
    id: str
    statement: str
    note: str
    reconsider: Optional[str] = None
    effect: Optional[str] = None

class BriefingItem(BaseModel):
    id: str
    developmentId: str
    storyId: str
    storyTitle: str
    headline: str
    summary: Optional[str] = None
    whatChanged: Optional[str] = None
    continuity: Optional[Continuity] = None
    whyItMatters: Optional[str] = None
    couldChange: Optional[str] = None
    relevanceBasis: Optional[RelevanceBasis] = None
    relevanceScore: float
    attention: Attention
    assumption: Optional[AssumptionNote] = None
    evidenceStrength: Optional[str] = None
    worldSignificance: Optional[float] = None
    ambitionId: Optional[str] = None
    ambitionTitle: Optional[str] = None
    occurredAt: str
    category: Optional[str] = None
    geography: Optional[str] = None
    sources: List[BriefingSource]
    imageUrl: Optional[str] = None
    tracked: bool

background_builds: Dict[str, asyncio.Task] = {}

class RefreshOptions(BaseModel):
    storyId: Optional[str] = None
    background: bool = False

async def refresh_briefing(user_id: str, options: RefreshOptions = RefreshOptions()) -> None:
    if not options.background:
        await build_briefing(user_id, {"storyId": options.storyId})
        return
        
    previous = background_builds.get(user_id)
    
    async def bg_task():
        if previous:
            try:
                await previous
            except Exception:
                pass
        try:
            await build_briefing(user_id, {"storyId": options.storyId})
        except Exception as err:
            log.error('background briefing build failed', {"userId": user_id, "err": str(err)})
            
    task = asyncio.create_task(bg_task())
    background_builds[user_id] = task
    
    def cleanup(t):
        if background_builds.get(user_id) == task:
            del background_builds[user_id]
            
    task.add_done_callback(cleanup)

def ist_date(now: Optional[datetime] = None) -> str:
    if not now:
        from datetime import timezone
        now = datetime.now(timezone.utc)
    # Add 5.5 hours
    dt = now + timedelta(hours=5, minutes=30)
    return dt.isoformat()[:10]

async def load_images(development_ids: List[str]) -> Dict[str, str]:
    images = {}
    if len(development_ids) == 0:
        return images
        
    res = supabase.table('story_sources').select('development_id, articles(image_url)')\
        .in_('development_id', development_ids).execute()
    rows = unwrap('sources.images', res)
    rows = rows if isinstance(rows, list) else [rows]
    
    for row in rows:
        dev_id = row.get('development_id')
        articles = row.get('articles')
        if not articles:
            continue
        url = articles.get('image_url')
        if dev_id and url and len(url) <= 600 and url.startswith('https://') and dev_id not in images:
            images[dev_id] = url
            
    return images

async def load_sources(development_ids: List[str]) -> Dict[str, List[BriefingSource]]:
    by_source = {}
    if len(development_ids) == 0:
        return by_source
        
    res = supabase.table('story_sources')\
        .select('development_id, url, title, publisher, published_at')\
        .in_('development_id', development_ids).execute()
        
    rows = unwrap('sources.load', res)
    rows = rows if isinstance(rows, list) else [rows]
    
    for row in rows:
        dev_id = row.get('development_id')
        if not dev_id:
            continue
        if dev_id not in by_source:
            by_source[dev_id] = []
        by_source[dev_id].append(BriefingSource(
            name=row.get('publisher') or 'Source',
            url=row.get('url'),
            title=row.get('title'),
            publishedAt=row.get('published_at')
        ))
        
    return by_source

def latest_per_story(shown: List[EvaluatedDevelopment], devs: Dict[str, DevelopmentRecord]) -> List[EvaluatedDevelopment]:
    best: Dict[str, EvaluatedDevelopment] = {}
    
    for item in shown:
        current = best.get(item.storyId)
        
        dev_item = devs.get(item.developmentId)
        item_time = datetime.fromisoformat(dev_item.occurred_at.replace('Z', '+00:00')).timestamp() if dev_item else 0.0
        
        dev_curr = devs.get(current.developmentId) if current else None
        curr_time = datetime.fromisoformat(dev_curr.occurred_at.replace('Z', '+00:00')).timestamp() if dev_curr else -1.0
        
        if not current or item_time > curr_time:
            best[item.storyId] = item
            
    res = list(best.values())
    
    def sort_key(a: EvaluatedDevelopment):
        attn_a = ATTENTION_RANK.get(a.decision.attention or 'fyi', 2)
        basis_a = 1 if a.decision.basis == 'general' else 0
        rel_a = a.decision.personalRelevance
        return (attn_a, basis_a, -rel_a)
        
    res.sort(key=sort_key)
    return res

class BriefingBuildResult(BaseModel):
    reportId: str
    items: int
    evaluated: int
    failed: int

async def build_briefing(user_id: str, options: Optional[Any] = None) -> BriefingBuildResult:
    on_progress = options if callable(options) else (options.get('onProgress') if isinstance(options, dict) else None)
    opts_dict = options if isinstance(options, dict) else {}
        
    ctx = await load_user_context(user_id)
    since = datetime.utcnow() - timedelta(days=WINDOW_DAYS)
    
    story_id = opts_dict.get('storyId')
    devs = await load_developments(storyId=story_id, limit=50) if story_id else await load_developments(since=since, limit=150)
    dev_by_id = {d.id: d for d in devs}
    
    eval_res = await evaluate_many(ctx, devs, lambda done, total: on_progress(f"Matching events to your ambitions ({done} of {total})") if on_progress else None)
    evaluated = eval_res['evaluated']
    failed = eval_res['failed']
    
    if on_progress:
        on_progress('Checking for unavoidable world events')
        
    world_events = [] if story_id else await pick_must_know_events(ctx, devs, evaluated)
    
    shown = latest_per_story([e for e in evaluated if e.decision.show] + world_events, dev_by_id)
    sources = await load_sources([s.developmentId for s in shown])
    
    res = supabase.table('daily_reports').upsert({
        "user_id": user_id,
        "report_date": ist_date(),
        "generated_at": datetime.utcnow().isoformat() + "Z"
    }).execute()
    report = unwrap('report.upsert', res)
    report = report[0] if isinstance(report, list) else report
    report_id = report['id']
    
    keep_ids = [s.developmentId for s in shown]
    
    existing_res = supabase.table('report_items').select('id, development_id').eq('report_id', report_id).execute()
    existing = unwrap('report_items.existing', existing_res)
    existing = existing if isinstance(existing, list) else [existing]
    
    scope = {d.id for d in devs}
    stale = [r['id'] for r in existing if r['development_id'] in scope and r['development_id'] not in keep_ids]
    
    if len(stale) > 0:
        supabase.table('report_items').delete().in_('id', stale).execute()
        
    if len(shown) > 0:
        rows = []
        for item in shown:
            dev = dev_by_id.get(item.developmentId)
            if not dev:
                raise Exception(f"development {item.developmentId} missing from window")
                
            rows.append({
                "report_id": report_id,
                "development_id": dev.id,
                "story_id": dev.story_id,
                "ambition_id": item.decision.ambitionId,
                "headline": dev.headline,
                "summary": dev.summary,
                "what_changed": dev.what_changed,
                "continuity": dev.continuity,
                "why_it_matters": item.decision.whyItMatters,
                "could_change": item.decision.couldChange,
                "relevance_reason": "; ".join([f.detail for f in item.decision.factors]) if item.decision.factors else None,
                "relevance_basis": item.decision.basis,
                "attention": item.decision.attention,
                "assumption_id": item.decision.assumption.assumptionId if item.decision.assumption and item.decision.assumption.effect == 'challenges' else None,
                "assumption_note": item.decision.assumption.reason if item.decision.assumption and item.decision.assumption.effect == 'challenges' else None,
                "personal_relevance": item.decision.personalRelevance,
                "world_significance": dev.world_significance,
                "confidence": item.decision.personalRelevance,
                "sources": [s.dict() for s in sources.get(dev.id, [])],
            })
        res_upsert = supabase.table('report_items').upsert(rows).execute()
        unwrap_void('report_items.upsert', res_upsert)
        
    log.info('briefing built', {"userId": user_id, "reportId": report_id, "items": len(shown), "evaluated": len(evaluated), "failed": failed})
    return BriefingBuildResult(reportId=report_id, items=len(shown), evaluated=len(evaluated), failed=failed)

async def get_latest_report(user_id: str) -> Optional[Dict[str, Any]]:
    res = supabase.table('daily_reports').select('*')\
        .eq('user_id', user_id).order('report_date', desc=True).limit(1).maybe_single().execute()
        
    row = unwrap_maybe('report.latest', res)
    if row:
        return {
            "id": row['id'],
            "reportDate": row['report_date'],
            "generatedAt": row['generated_at'],
            "executiveSummary": row.get('executive_summary'),
            "trajectoryAssessment": row.get('trajectory_assessment'),
            "historicalConnections": row.get('historical_connections'),
            "opportunities": row.get('opportunities'),
            "risks": row.get('risks'),
            "recommendedActions": row.get('recommended_actions'),
            "sources": row.get('sources'),
            "uncertainties": row.get('uncertainties'),
            "researchPlan": row.get('research_plan'),
            "activityTrace": row.get('activity_trace'),
            "modelUsed": row.get('model_used')
        }
    return None

async def get_briefing_items(user_id: str, limit: int = 20) -> Dict[str, Any]:
    report = await get_latest_report(user_id)
    if not report:
        return {"report": None, "items": []}
        
    items = await get_report_items(user_id, report, limit)
    return {"report": report, "items": items}

async def get_report_for_date(user_id: str, date: str) -> Optional[Dict[str, Any]]:
    res = supabase.table('daily_reports').select('id, report_date, generated_at')\
        .eq('user_id', user_id).eq('report_date', date).order('generated_at', desc=True).limit(1).execute()
    rows = unwrap_maybe('report.byDate', res)
    if isinstance(rows, list) and len(rows) > 0:
        row = rows[0]
        return {"id": row['id'], "reportDate": row['report_date'], "generatedAt": row['generated_at']}
    elif isinstance(rows, dict) and rows.get('id'):
        return {"id": rows['id'], "reportDate": rows['report_date'], "generatedAt": rows['generated_at']}
    return None

async def list_report_days(user_id: str, limit: int = 30) -> List[Dict[str, Any]]:
    res = supabase.table('daily_reports').select('id, report_date')\
        .eq('user_id', user_id).order('report_date', desc=True).limit(limit).execute()
    reports = unwrap('report.days', res)
    reports = reports if isinstance(reports, list) else [reports]
    
    if len(reports) == 0:
        return []
        
    report_ids = [r['id'] for r in reports]
    res_items = supabase.table('report_items').select('report_id').in_('report_id', report_ids).execute()
    rows = unwrap('report_items.counts', res_items)
    rows = rows if isinstance(rows, list) else [rows]
    
    counts = {}
    for r in rows:
        r_id = r['report_id']
        counts[r_id] = counts.get(r_id, 0) + 1
        
    return [{"date": r['report_date'], "items": counts.get(r['id'], 0)} for r in reports]

async def get_report_items(user_id: str, report: Dict[str, Any], limit: int = 20) -> List[BriefingItem]:
    select = 'id, development_id, story_id, ambition_id, headline, summary, what_changed, continuity, why_it_matters, could_change, relevance_basis, attention, assumption_id, assumption_note, personal_relevance, world_significance, sources, developments(occurred_at, evidence_strength), stories(title, category, geography), ambitions(title), ambition_assumptions(statement)'
    
    res = supabase.table('report_items').select(select).eq('report_id', report['id']).execute()
    rows = unwrap('report_items.list', res)
    rows = rows if isinstance(rows, list) else [rows]
    
    res_tracked = supabase.table('tracked_stories').select('story_id').eq('user_id', user_id).execute()
    tracked = unwrap('tracked.ids', res_tracked)
    tracked = tracked if isinstance(tracked, list) else [tracked]
    tracked_ids = {t['story_id'] for t in tracked}
    
    images = await load_images([r['development_id'] for r in rows])
    
    items = []
    from datetime import timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    for r in rows:
        dev = r.get('developments') or {}
        if isinstance(dev, list):
            dev = dev[0] if dev else {}
        st = r.get('stories') or {}
        if isinstance(st, list):
            st = st[0] if st else {}
        amb = r.get('ambitions') or {}
        if isinstance(amb, list):
            amb = amb[0] if amb else {}
        amb_ass = r.get('ambition_assumptions') or {}
        if isinstance(amb_ass, list):
            amb_ass = amb_ass[0] if amb_ass else {}
        
        ass = None
        if r.get('assumption_id') and amb_ass and r.get('assumption_note'):
            ass = AssumptionNote(
                id=r['assumption_id'],
                statement=amb_ass.get('statement', '') if isinstance(amb_ass, dict) else '',
                note=r['assumption_note'],
                reconsider=r.get('assumption_reconsider'),
                effect=r.get('assumption_effect')
            )
            
        raw_sources = r.get('sources') or []
        parsed_sources = []
        for s in raw_sources:
            if isinstance(s, dict):
                parsed_sources.append(BriefingSource(
                    name=s.get('name') or s.get('title') or s.get('url') or 'Source',
                    url=s.get('url') or '',
                    title=s.get('title'),
                    publishedAt=s.get('publishedAt') or s.get('published_at')
                ))
            elif isinstance(s, str):
                parsed_sources.append(BriefingSource(name=s, url=s))

        items.append(BriefingItem(
            id=r['id'],
            developmentId=r['development_id'],
            storyId=r['story_id'],
            storyTitle=st.get('title', '') if isinstance(st, dict) else '',
            headline=r['headline'],
            summary=r.get('summary'),
            whatChanged=r.get('what_changed'),
            continuity=r.get('continuity'),
            whyItMatters=r.get('why_it_matters'),
            couldChange=r.get('could_change'),
            relevanceBasis=r.get('relevance_basis'),
            relevanceScore=r.get('personal_relevance', 0) or 0.0,
            attention=r.get('attention', 'fyi'),
            assumption=ass,
            evidenceStrength=dev.get('evidence_strength') if isinstance(dev, dict) else None,
            worldSignificance=r.get('world_significance'),
            ambitionId=r.get('ambition_id'),
            ambitionTitle=amb.get('title') if isinstance(amb, dict) else None,
            occurredAt=dev.get('occurred_at') if isinstance(dev, dict) and dev.get('occurred_at') else (report.get('generatedAt') or now_iso),
            category=st.get('category') if isinstance(st, dict) else None,
            geography=st.get('geography') if isinstance(st, dict) else None,
            sources=parsed_sources,
            imageUrl=images.get(r['development_id']),
            tracked=r['story_id'] in tracked_ids
        ))
        
    items.sort(key=lambda i: (ATTENTION_RANK.get(i.attention, 2), -i.relevanceScore))
    return items[:limit]
