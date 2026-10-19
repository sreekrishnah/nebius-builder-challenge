import hashlib
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from datetime import datetime, timezone

from src.ai.guard import clean_model_text
from src.ai.tasks import summarize_period
from src.domain.attention import ATTENTION_RANK
from src.domain.periods import Bucket, Period, bucket_for, bucket_key, ist_day
from src.domain.types import Attention, Continuity
from src.lib.errors import errors
from src.lib.logger import create_logger
from src.lib.supabase import supabase, unwrap, unwrap_maybe, unwrap_void
from src.services.ambitions import get_profile, list_ambitions

log = create_logger('evolution')

QUIET_WINDOW_DAYS = 30
ITEMS_LIMIT = 2000
MAX_NEW_SUMMARIES = 12
MAX_ENTRIES_PER_SUMMARY = 12
MAX_SUMMARY_CHARS = 500

class EvolutionEntry(BaseModel):
    developmentId: str
    storyId: str
    storyTitle: str
    headline: str
    continuity: Optional[Continuity] = None
    occurredAt: str
    fetchedOn: str
    whyItMatters: Optional[str] = None
    attention: Optional[Attention] = None

class ChangedUnderstanding(BaseModel):
    assumptionId: str
    statement: str
    challengedAt: Optional[str] = None
    reason: Optional[str] = None
    headline: Optional[str] = None

class EvolutionPeriod(BaseModel):
    period: Period
    start: str
    summary: Optional[str] = None
    developments: List[EvolutionEntry]

class EvolutionQuiet(BaseModel):
    lastCheckedAt: Optional[str] = None
    observedDays: int
    windowDays: int
    linkedInWindow: int
    lastLinkedAt: Optional[str] = None

class Evolution(BaseModel):
    ambitionId: str
    quiet: EvolutionQuiet
    changedUnderstanding: List[ChangedUnderstanding]
    periods: List[EvolutionPeriod]

async def last_completed_run(user_id: str) -> Optional[str]:
    job_res = supabase.table('fetch_jobs').select('completed_at')\
        .eq('user_id', user_id).eq('status', 'COMPLETED').order('completed_at', desc=True).limit(1).maybe_single().execute()
    batch_res = supabase.table('batch_runs').select('completed_at')\
        .eq('status', 'COMPLETED').order('completed_at', desc=True).limit(1).maybe_single().execute()
        
    job = unwrap_maybe('jobs.last', job_res)
    batch = unwrap_maybe('batch.last', batch_res)
    
    times = []
    if job and job.get('completed_at'): times.append(job['completed_at'])
    if batch and batch.get('completed_at'): times.append(batch['completed_at'])
    
    times.sort()
    return times[-1] if len(times) > 0 else None

class PeriodRow(BaseModel):
    period: Period
    period_start: str
    summary: str
    items_hash: str

async def load_entries(user_id: str, ambition_id: str) -> List[EvolutionEntry]:
    select = 'development_id, headline, continuity, why_it_matters, attention, story_id, stories(title), developments(occurred_at), daily_reports!inner(report_date, user_id)'
    res = supabase.table('report_items').select(select)\
        .eq('ambition_id', ambition_id)\
        .eq('daily_reports.user_id', user_id)\
        .limit(ITEMS_LIMIT).execute()
        
    rows = unwrap('evolution.items', res)
    rows = rows if isinstance(rows, list) else [rows]
    
    first = {}
    for r in rows:
        dr = r.get('daily_reports') or {}
        fetched_on = dr.get('report_date', '')
        dev_id = r['development_id']
        
        existing = first.get(dev_id)
        if existing and existing.fetchedOn <= fetched_on:
            continue
            
        st = r.get('stories') or {}
        devs = r.get('developments') or {}
        
        first[dev_id] = EvolutionEntry(
            developmentId=dev_id,
            storyId=r.get('story_id', ''),
            storyTitle=st.get('title', ''),
            headline=r.get('headline', ''),
            continuity=r.get('continuity'),
            occurredAt=devs.get('occurred_at') or f"{fetched_on}T00:00:00Z",
            fetchedOn=fetched_on,
            whyItMatters=r.get('why_it_matters', '').strip() if r.get('why_it_matters') else None,
            attention=r.get('attention')
        )
        
    entries = list(first.values())
    entries.sort(key=lambda x: (x.fetchedOn, -ATTENTION_RANK.get(x.attention or 'fyi', 2)), reverse=True)
    return entries

class Group(BaseModel):
    bucket: Bucket
    entries: List[EvolutionEntry]

def group_by_bucket(entries: List[EvolutionEntry], today: str) -> List[Group]:
    groups_dict = {}
    for entry in entries:
        b = bucket_for(entry.fetchedOn, today)
        key = bucket_key(b)
        if key not in groups_dict:
            groups_dict[key] = Group(bucket=b, entries=[])
        groups_dict[key].entries.append(entry)
        
    groups = list(groups_dict.values())
    groups.sort(key=lambda x: x.bucket.start, reverse=True)
    return groups

def hash_of(entries: List[EvolutionEntry]) -> str:
    dev_ids = [e.developmentId for e in entries]
    dev_ids.sort()
    return hashlib.sha1(",".join(dev_ids).encode('utf-8')).hexdigest()

def strongest(entries: List[EvolutionEntry]) -> List[EvolutionEntry]:
    res = list(entries)
    res.sort(key=lambda x: ATTENTION_RANK.get(x.attention or 'fyi', 2))
    return res

PERIOD_LABEL = {
    'day': lambda start: f"the day {start}",
    'week': lambda start: f"the week starting {start}",
    'month': lambda start: f"the month {start[:7]}",
    'year': lambda start: f"the year {start[:4]}"
}

async def load_period_rows(user_id: str, ambition_id: str) -> List[PeriodRow]:
    res = supabase.table('ambition_period_summaries')\
        .select('period, period_start, summary, items_hash')\
        .eq('user_id', user_id).eq('ambition_id', ambition_id).execute()
        
    rows = unwrap('evolution.periods', res)
    rows = rows if isinstance(rows, list) else [rows]
    return [PeriodRow(**r) for r in rows]

async def refresh_period_summaries(user_id: str, ambition: Dict[str, str], language: str) -> int:
    today = ist_day(datetime.utcnow())
    entries = await load_entries(user_id, ambition['id'])
    groups = group_by_bucket(entries, today)
    
    period_rows = await load_period_rows(user_id, ambition['id'])
    stored = {f"{r.period}:{r.period_start}": r for r in period_rows}
    
    written = 0
    for group in groups:
        if written >= MAX_NEW_SUMMARIES:
            break
            
        h = hash_of(group.entries)
        bk = bucket_key(group.bucket)
        if bk in stored and stored[bk].items_hash == h:
            continue
            
        try:
            strong_entries = strongest(group.entries)[:MAX_ENTRIES_PER_SUMMARY]
            raw_summary = await summarize_period(
                ambition=ambition['title'],
                periodLabel=PERIOD_LABEL[group.bucket.period](group.bucket.start),
                language=language,
                entries=[{"date": e.fetchedOn, "headline": e.headline, "why": e.whyItMatters} for e in strong_entries]
            )
            summary = clean_model_text(raw_summary, MAX_SUMMARY_CHARS)
            if not summary:
                continue
                
            res = supabase.table('ambition_period_summaries').upsert({
                "user_id": user_id,
                "ambition_id": ambition['id'],
                "period": group.bucket.period,
                "period_start": group.bucket.start,
                "summary": summary,
                "items_hash": h,
                "generated_at": datetime.utcnow().isoformat() + "Z"
            }).execute()
            unwrap_void('evolution.summary.store', res)
            written += 1
        except Exception as err:
            log.warn('period summary skipped', {"userId": user_id, "ambitionId": ambition['id'], "bucket": bk, "err": str(err)})
            
    current = {bucket_key(g.bucket) for g in groups}
    for key, row in stored.items():
        if key in current:
            continue
        res_del = supabase.table('ambition_period_summaries').delete()\
            .eq('user_id', user_id).eq('ambition_id', ambition['id'])\
            .eq('period', row.period).eq('period_start', row.period_start).execute()
        unwrap_void('evolution.summary.prune', res_del)
        
    return written

async def refresh_history(user_id: str) -> None:
    ambs = await list_ambitions(user_id)
    profile = await get_profile(user_id)
    language = profile.report_language if profile and profile.report_language else 'English'
    
    for a in ambs:
        if a.status == 'active':
            await refresh_period_summaries(user_id, {"id": a.id, "title": a.title}, language)

async def get_evolution(user_id: str, ambition_id: str) -> Evolution:
    res = supabase.table('ambitions').select('id').eq('id', ambition_id).eq('user_id', user_id).maybe_single().execute()
    owns = unwrap_maybe('evolution.ambition', res)
    if not owns:
        raise errors.not_found('Ambition')
        
    import asyncio
    t_entries = load_entries(user_id, ambition_id)
    t_stored = load_period_rows(user_id, ambition_id)
    chal_res = supabase.table('ambition_assumptions')\
        .select('id, statement, challenged_at, challenge_reason, developments:challenged_by_development_id(headline)')\
        .eq('user_id', user_id).eq('ambition_id', ambition_id).eq('status', 'challenged')\
        .order('challenged_at', desc=True).execute()
    first_eval_res = supabase.table('world_coverage').select('evaluated_at').eq('user_id', user_id).order('evaluated_at', desc=False).limit(1).maybe_single().execute()
    t_last_chk = last_completed_run(user_id)
    
    entries, stored_periods, last_checked_at = await asyncio.gather(t_entries, t_stored, t_last_chk)
    
    challenged = unwrap('evolution.challenged', chal_res)
    challenged = challenged if isinstance(challenged, list) else [challenged]
    
    first_eval = unwrap_maybe('evolution.first', first_eval_res)
    
    summaries = {f"{r.period}:{r.period_start}": r.summary for r in stored_periods}
    today = ist_day(datetime.utcnow())
    
    periods = []
    for group in group_by_bucket(entries, today):
        bk = bucket_key(group.bucket)
        s = summaries.get(bk)
        if not s:
            se = strongest(group.entries)
            s = se[0].whyItMatters if len(se) > 0 else None
            
        periods.append(EvolutionPeriod(
            period=group.bucket.period,
            start=group.bucket.start,
            summary=s,
            developments=group.entries
        ))
        
    import time
    window_start_ts = time.time() - QUIET_WINDOW_DAYS * 86400
    window_start = ist_day(datetime.fromtimestamp(window_start_ts, tz=timezone.utc))
    
    in_window = [e for e in entries if e.fetchedOn >= window_start]
    latest_day = entries[0].fetchedOn if len(entries) > 0 else None
    
    first_at = first_eval.get('evaluated_at') if first_eval else None
    observed_days = 0
    if first_at:
        first_ts = datetime.fromisoformat(first_at.replace('Z', '+00:00')).timestamp()
        diff_days = (time.time() - first_ts) / 86400.0
        observed_days = min(QUIET_WINDOW_DAYS, max(1, int(diff_days + 0.999)))
        
    ch_understanding = []
    for c in challenged:
        devs = c.get('developments') or {}
        ch_understanding.append(ChangedUnderstanding(
            assumptionId=c['id'],
            statement=c['statement'],
            challengedAt=c.get('challenged_at'),
            reason=c.get('challenge_reason'),
            headline=devs.get('headline')
        ))
        
    return Evolution(
        ambitionId=ambition_id,
        quiet=EvolutionQuiet(
            lastCheckedAt=last_checked_at,
            observedDays=observed_days,
            windowDays=QUIET_WINDOW_DAYS,
            linkedInWindow=len(in_window),
            lastLinkedAt=f"{latest_day}T12:00:00Z" if latest_day else None
        ),
        changedUnderstanding=ch_understanding,
        periods=periods
    )
