from typing import List
from datetime import datetime, timedelta, timezone
from src.domain.lens import CoverageEntry, FeedbackEntry, Lens, build_lens
from src.lib.supabase import supabase, unwrap

WINDOW_DAYS = 14
FEEDBACK_LIMIT = 20

async def get_lens(user_id: str) -> Lens:
    since = (datetime.utcnow() - timedelta(days=WINDOW_DAYS)).isoformat() + "Z"
    
    cov_res = supabase.table('world_coverage').select(
        'development_id, world_significance, personal_relevance, shown_to_user, suppressed, suppressed_reason, relevance_basis, attention, relevance_reasons, developments!inner(headline, occurred_at, story_id, stories(title))'
    ).eq('user_id', user_id).gte('developments.occurred_at', since).execute()
    
    fb_res = supabase.table('feedback').select(
        'id, created_at, feedback_type, reason, stories(title)'
    ).eq('user_id', user_id).gte('created_at', since).order('created_at', desc=True).limit(FEEDBACK_LIMIT).execute()
    
    coverage_rows = unwrap('lens.coverage', cov_res)
    coverage_rows = coverage_rows if isinstance(coverage_rows, list) else [coverage_rows]
    
    feedback_rows = unwrap('lens.feedback', fb_res)
    feedback_rows = feedback_rows if isinstance(feedback_rows, list) else [feedback_rows]
    
    coverage = []
    for r in coverage_rows:
        devs = r.get('developments')
        if not devs:
            continue
            
        stories = devs.get('stories') or {}
        rr = r.get('relevance_reasons') or {}
        
        att = r.get('attention')
        if att not in ('act', 'know', 'fyi'):
            att = None
            
        coverage.append(CoverageEntry(
            developmentId=r.get('development_id', ''),
            storyId=devs.get('story_id', ''),
            storyTitle=stories.get('title', ''),
            headline=devs.get('headline', ''),
            occurredAt=devs.get('occurred_at', ''),
            worldSignificance=r.get('world_significance'),
            personalRelevance=r.get('personal_relevance') or 0.0,
            shown=r.get('shown_to_user', False),
            suppressed=r.get('suppressed', False),
            suppressedReason=r.get('suppressed_reason'),
            suppressedMemoryId=rr.get('suppressedMemoryId'),
            basis=r.get('relevance_basis'),
            attention=att
        ))
        
    feedback = []
    for f in feedback_rows:
        fb_type = f.get('feedback_type')
        if fb_type:
            stories = f.get('stories') or {}
            feedback.append(FeedbackEntry(
                id=f.get('id', ''),
                createdAt=f.get('created_at', ''),
                feedbackType=fb_type,
                storyTitle=stories.get('title'),
                reason=f.get('reason')
            ))
            
    return build_lens(coverage, feedback)
