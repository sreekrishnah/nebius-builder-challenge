import asyncio
from typing import List, Dict, Any, Optional, Callable, Set
from pydantic import BaseModel
from datetime import datetime, timedelta, timezone

from src.lib.gemini import run_patiently
from src.lib.errors import AppError, errors
from src.lib.news import search_news, NewsArticle
from src.lib.logger import create_logger
from src.lib.supabase import supabase, unwrap, unwrap_maybe, unwrap_void
from src.providers.intelligence import ResearchPlanInput
from src.services.briefing import build_briefing
from src.services.evolution import refresh_history
from src.services.evidence import execute_multi_step_research
from src.services.ingest import IngestStats, empty_stats, ingest_articles
from src.services.memory import archive_expired_memory
from src.services.relevance import load_user_context, load_developments
from src.services.report_intelligence import synthesize_daily_report, save_structured_report
from src.services.research_planner import build_research_plan
from src.services.script import generate_briefing_script

log = create_logger('pipeline')

LOOKBACK_DAYS = 7
SEARCH_SPACING_MS = 1500
QUERIES_PER_AMBITION = 5
MAX_QUERIES = 8
STALE_HEARTBEAT_MS = 10 * 60 * 1000
HEARTBEAT_INTERVAL_MS = 30 * 1000
RUNS_PER_DAY = 2

ProgressReporter = Callable[[str], None]

class JobStatus(BaseModel):
    id: str
    status: str
    startedAt: Optional[str] = None
    completedAt: Optional[str] = None
    error: Optional[str] = None
    progress: Optional[str] = None
    stats: Optional[Dict[str, Any]] = None

class JobRow(BaseModel):
    id: str
    status: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None
    progress: Optional[str] = None
    heartbeat_at: Optional[str] = None
    stats: Optional[Dict[str, Any]] = None

def effective_status(row: JobRow) -> str:
    if row.status != 'PROCESSING':
        return row.status
    
    last_seen_str = row.heartbeat_at or row.started_at
    if not last_seen_str:
        last_seen = 0.0
    else:
        last_seen = datetime.fromisoformat(last_seen_str.replace('Z', '+00:00')).timestamp() * 1000
        
    if (datetime.now(timezone.utc).timestamp() * 1000) - last_seen > STALE_HEARTBEAT_MS:
        return 'FAILED'
    return 'PROCESSING'

def to_status(row: JobRow) -> JobStatus:
    eff_stat = effective_status(row)
    err = row.error
    if not err and eff_stat == 'FAILED' and row.status == 'PROCESSING':
        err = 'The refresh stopped unexpectedly. Please try again.'
        
    return JobStatus(
        id=row.id,
        status=eff_stat,
        startedAt=row.started_at,
        completedAt=row.completed_at,
        error=err,
        progress=row.progress,
        stats=row.stats
    )

async def get_latest_job(user_id: str) -> Optional[JobStatus]:
    res = supabase.table('fetch_jobs')\
        .select('id, status, started_at, completed_at, error, progress, heartbeat_at, stats')\
        .eq('user_id', user_id)\
        .order('created_at', desc=True).limit(1).maybe_single().execute()
        
    row = unwrap_maybe('jobs.latest', res)
    return to_status(JobRow(**row)) if row else None

async def search_concepts(user_id: str) -> List[str]:
    res = supabase.table('ambitions')\
        .select('id, ambition_context(search_concepts)')\
        .eq('user_id', user_id).eq('status', 'active').execute()
        
    rows = unwrap('ambition_context.load', res)
    rows = rows if isinstance(rows, list) else [rows]
    
    queries = []
    for ambition in rows:
        ac = ambition.get('ambition_context')
        if ac and len(ac) > 0 and isinstance(ac, list):
            concepts = ac[0].get('search_concepts') or []
            queries.extend(concepts[:QUERIES_PER_AMBITION])
            
    # Distinct and filter empty
    seen = set()
    unique = []
    for q in queries:
        qt = q.strip()
        if qt and qt not in seen:
            seen.add(qt)
            unique.append(qt)
            
    return unique[:MAX_QUERIES]

async def collect_news(queries: List[str], report: ProgressReporter) -> Dict[str, Any]:
    articles = []
    failures = []
    for index, query in enumerate(queries):
        report(f"Searching the events ({index + 1} of {len(queries)})")
        if len(articles) > 0 or len(failures) > 0:
            await asyncio.sleep(SEARCH_SPACING_MS / 1000.0)
        try:
            res = await search_news(query, LOOKBACK_DAYS)
            articles.extend(res)
        except Exception as err:
            msg = err.diagnostic if isinstance(err, AppError) and err.diagnostic else (err.safeMessage if isinstance(err, AppError) else str(err))
            failures.append(msg)
            
    if len(articles) == 0 and len(failures) == len(queries) and len(queries) > 0:
        raise errors.source(' | '.join(failures))
        
    return {"articles": articles, "errors": failures}

class RunResult(BaseModel):
    stats: IngestStats
    sourceErrors: List[str]
    briefing: Any  # Replaced with Any to avoid circular dependency before we implement briefing
    scripts: int

async def _run_pipeline_logic(user_ids: List[str], report: ProgressReporter) -> RunResult:
    stats = empty_stats()
    source_errors: List[str] = []
    all_persisted_sources = []
    activity_traces = []
    research_plans: Dict[str, Any] = {}

    for user_id in user_ids:
        try:
            report('Loading your ambition profile and memory')
            ctx = await load_user_context(user_id)
            
            # Identify primary ambition
            primary_ambition = "Active Ambition"
            if ctx.ambitions:
                sorted_ambitions = sorted(ctx.ambitions.values(), key=lambda a: a.priority)
                primary_ambition = sorted_ambitions[0].title
                
            goals = [a.description for a in ctx.ambitions.values() if a.description]
            plan_input = ResearchPlanInput(
                ambition=primary_ambition,
                role=ctx.identity.get("role", "Professional"),
                activity=ctx.identity.get("activity", "Working"),
                geography=ctx.identity.get("geography", "Global"),
                timeline=ctx.identity.get("timeline", "Not specified"),
                goals=goals,
                preferences=[m.topic for m in ctx.memory if m.memory_type == "interest"],
                suppressions=[m.topic for m in ctx.memory if m.memory_type in ("suppression", "temporary_suppression")],
                recent_feedback=[m.content for m in ctx.memory if m.memory_type in ("interest", "suppression") and m.content],
                assumptions=[a.statement for alist in ctx.assumptions.values() for a in alist]
            )
            
            report('Formulating targeted research plan with Nemotron')
            plan = await build_research_plan(plan_input)
            research_plans[user_id] = plan
            
            # Multi-step research
            res_out = await execute_multi_step_research(plan, report)
            source_errors.extend(res_out["errors"])
            activity_traces.extend(res_out["activity_trace"])
            all_persisted_sources.extend(res_out["sources"])
            
            if res_out["articles"]:
                last_reported = -1
                def on_prog(done: int, total: int):
                    nonlocal last_reported
                    if done - last_reported >= 3 or done == 0:
                        last_reported = done
                        report(f"Reading and sorting articles ({done} of {total})")
                user_stats = await ingest_articles(res_out["articles"], on_prog)
                stats.received += user_stats.received
                stats.alreadyKnown += user_stats.alreadyKnown
                stats.rejected += user_stats.rejected
                stats.newStories += user_stats.newStories
                stats.newDevelopments += user_stats.newDevelopments
                stats.corroborations += user_stats.corroborations
                stats.noChange += user_stats.noChange
                stats.failed += user_stats.failed
        except Exception as err:
            log.error('research planning and retrieval failed', {"userId": user_id, "err": str(err)})
            source_errors.append(f"Research retrieval error: {str(err)}")
            # Fallback to search_concepts if plan generation fails
            c = await search_concepts(user_id)
            if c:
                news = await collect_news(c, report)
                source_errors.extend(news["errors"])
                user_stats = await ingest_articles(news["articles"], lambda d, t: None)
                stats.received += user_stats.received
                
    briefing = None
    scripts = 0
    for user_id in user_ids:
        briefing = await build_briefing(user_id, report)
        
        # Synthesize structured intelligence report with NVIDIA Nemotron
        report('Synthesizing structured intelligence report with Nemotron')
        try:
            ctx = await load_user_context(user_id)
            user_amb = next(iter(ctx.ambitions.values()), None) if ctx.ambitions else None
            ambition_dict = {
                "ambition": user_amb.title if user_amb else "Personal Ambition",
                "role": ctx.identity.get("role", "Professional"),
                "activity": ctx.identity.get("activity", "Working"),
                "goals": [a.description for a in ctx.ambitions.values() if a.description],
                "preferences": [m.topic for m in ctx.memory if m.memory_type == "interest"],
                "suppressions": [m.topic for m in ctx.memory if m.memory_type in ("suppression", "temporary_suppression")],
                "assumptions": [a.statement for alist in ctx.assumptions.values() for a in alist]
            }
            
            # Load window developments for synthesis
            since = datetime.utcnow() - timedelta(days=LOOKBACK_DAYS)
            recent_devs = await load_developments(since=since, limit=30)
            devs_payload = [
                {
                    "headline": d.headline,
                    "summary": d.summary,
                    "what_changed": d.what_changed,
                    "category": d.stories.get("category") if d.stories else "General",
                    "occurred_at": d.occurred_at,
                    "sources": [s.get("url") for s in (d.story_sources or []) if s.get("url")]
                }
                for d in recent_devs
            ]
            
            # Load historical memory
            prior_reports_res = supabase.table('daily_reports').select('id, report_date, summary, executive_summary')\
                .eq('user_id', user_id).neq('id', briefing.reportId).order('created_at', desc=True).limit(5).execute()
            historical_reports = unwrap_maybe('reports.history', prior_reports_res) or []
            
            user_plan = research_plans.get(user_id)
            structured_rep = await synthesize_daily_report(
                user_id=user_id,
                ambition_ctx=ambition_dict,
                developments=devs_payload,
                historical_context=historical_reports if isinstance(historical_reports, list) else [historical_reports],
                persisted_sources=all_persisted_sources,
                research_plan_meta=user_plan.dict() if user_plan else None,
                activity_trace=activity_traces
            )
            
            await save_structured_report(
                report_id=briefing.reportId,
                structured_report=structured_rep,
                research_plan_meta=user_plan.dict() if user_plan else None,
                activity_trace=activity_traces
            )
        except Exception as err:
            log.error('structured report synthesis failed', {"userId": user_id, "err": str(err)})

        report('Writing your spoken briefing script')
        try:
            if await generate_briefing_script(user_id):
                scripts += 1
        except Exception as err:
            log.error('script generation failed', {"userId": user_id, "err": str(err)})
            
    for user_id in user_ids:
        report('Updating how your ambitions have changed')
        try:
            await refresh_history(user_id)
        except Exception as err:
            log.error('ambition history refresh failed', {"userId": user_id, "err": str(err)})
            
    await archive_expired_memory()
    return RunResult(stats=stats, sourceErrors=source_errors, briefing=briefing, scripts=scripts)

async def run_pipeline(user_ids: List[str], report: ProgressReporter = lambda m: None) -> RunResult:
    return await run_patiently(lambda: _run_pipeline_logic(user_ids, report))

async def start_user_run(user_id: str) -> str:
    alive_since = (datetime.utcnow() - timedelta(milliseconds=STALE_HEARTBEAT_MS)).isoformat() + "Z"
    
    # Or condition using postgrest syntax
    or_cond = f"heartbeat_at.gte.{alive_since},and(heartbeat_at.is.null,started_at.gte.{alive_since})"
    
    res = supabase.table('fetch_jobs').select('id')\
        .eq('user_id', user_id).eq('status', 'PROCESSING')\
        .or_(or_cond).limit(1).maybe_single().execute()
        
    recent = unwrap_maybe('jobs.running', res)
    if recent:
        raise AppError('ALREADY_RUNNING', 409, 'A refresh is already in progress.')
        
    allowed_res = supabase.rpc('consume_daily_run', {"p_user_id": user_id}).execute()
    # Pydantic doesn't expose the response object perfectly for errors, but typically supabase-py throws on rpc error
    allowed = allowed_res.data
    if not allowed:
        raise AppError('DAILY_LIMIT', 429, f"Daily refresh limit reached ({RUNS_PER_DAY} per day, including the scheduled 6 am run).")
        
    now = datetime.utcnow().isoformat() + "Z"
    insert_data = {"user_id": user_id, "trigger": 'MANUAL', "status": 'PROCESSING', "started_at": now, "heartbeat_at": now, "progress": 'Starting'}
    job_res = supabase.table('fetch_jobs').insert(insert_data).select('id').execute()
    job = unwrap('jobs.create', job_res)
    job = job[0] if isinstance(job, list) else job
    job_id = job['id']
    
    # Background task emulation
    async def bg_task():
        latest = 'Starting'
        
        async def beat():
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL_MS / 1000.0)
                try:
                    supabase.table('fetch_jobs').update({
                        "heartbeat_at": datetime.utcnow().isoformat() + "Z", 
                        "progress": latest
                    }).eq('id', job_id).execute()
                except Exception as e:
                    log.warn('job heartbeat failed', {"jobId": job_id, "err": str(e)})

        beat_task = asyncio.create_task(beat())
        
        try:
            def update_latest(msg: str):
                nonlocal latest
                latest = msg
                
            result = await run_pipeline([user_id], update_latest)
            stats_dict = result.stats.dict() if hasattr(result.stats, 'dict') else result.stats
            
            upd = {
                "status": 'COMPLETED',
                "completed_at": datetime.utcnow().isoformat() + "Z",
                "progress": 'Done',
                "stats": {
                    **stats_dict,
                    "sourceErrors": result.sourceErrors,
                    # We may need to serialize briefing properly here later
                    "briefing": getattr(result.briefing, 'dict', lambda: result.briefing)() if result.briefing else None,
                    "scripts": result.scripts
                }
            }
            res_upd = supabase.table('fetch_jobs').update(upd).eq('id', job_id).execute()
            unwrap_void('jobs.complete', res_upd)
        except Exception as err:
            message = err.safeMessage if isinstance(err, AppError) else 'Refresh failed.'
            log.error('pipeline run failed', {"userId": user_id, "jobId": job_id, "err": str(err)})
            supabase.table('fetch_jobs').update({
                "status": 'FAILED',
                "completed_at": datetime.utcnow().isoformat() + "Z",
                "error": message
            }).eq('id', job_id).execute()
        finally:
            beat_task.cancel()
            
    asyncio.create_task(bg_task())
    return job_id

async def fail_orphaned_jobs() -> None:
    cutoff = (datetime.utcnow() - timedelta(milliseconds=STALE_HEARTBEAT_MS)).isoformat() + "Z"
    or_cond = f"heartbeat_at.lt.{cutoff},and(heartbeat_at.is.null,started_at.lt.{cutoff})"
    
    try:
        supabase.table('fetch_jobs').update({
            "status": 'FAILED',
            "completed_at": datetime.utcnow().isoformat() + "Z",
            "error": 'Interrupted by a server restart.'
        }).eq('status', 'PROCESSING').or_(or_cond).execute()
    except Exception as e:
        log.error('failed to clean orphaned jobs', {"err": str(e)})
