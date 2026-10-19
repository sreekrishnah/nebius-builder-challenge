import asyncio
import json
from typing import Dict, Any, List, Optional, Set, Literal, Callable
from pydantic import BaseModel, Field

from src.ai.toolGuard import heard_request, is_changing_tool
from src.config.env import env
from src.lib.errors import errors
from src.lib.gemini import embed_text
from src.lib.logger import create_logger
from src.lib.supabase import supabase, unwrap, unwrap_maybe, rpc_rows
from src.services.ambitions import list_ambitions
from src.services.briefing import get_briefing_items, refresh_briefing, RefreshOptions, BriefingItem, get_latest_report
from src.services.dossier import DOSSIER_ITEMS, load_story_history
from src.services.feedback import FEEDBACK_TYPES, apply_feedback, FeedbackInput
from src.services.feedback_interpreter import interpret_and_apply_user_feedback
from src.services.memory import days_from_now, get_active_memory, get_identity, remember, MemoryInput
from src.services.relevance import evaluate_development, load_developments, load_user_context
from src.services.stories import get_timeline, list_tracked_stories, mark_seen, track_story, untrack_story

log = create_logger('live-tools')

class StoryRef(BaseModel):
    storyId: str
    developmentId: Optional[str] = None
    title: str

class SessionRefs:
    def __init__(self):
        self.by_ref: Dict[int, StoryRef] = {}
        self.by_story: Dict[str, int] = {}
        self.next = 1
        self.focus_story_id: Optional[str] = None
        self.discussed_story_ids: Set[str] = set()
        self.briefing: Optional[Any] = None
        self.heard: Optional[Callable[[], str]] = None
        self.tool_calls = 0

    def register(self, ref: Any) -> int:
        if isinstance(ref, dict):
            ref = StoryRef(**ref)
        existing = self.by_story.get(ref.storyId)
        if existing:
            self.by_ref[existing] = ref
            return existing
        number = self.next
        self.next += 1
        self.by_ref[number] = ref
        self.by_story[ref.storyId] = number
        return number

    def resolve(self, ref_number: Optional[int]) -> StoryRef:
        if ref_number is not None:
            found = self.by_ref.get(ref_number)
            if not found:
                raise errors.validation(f"Unknown story reference {ref_number}.")
            self.focus_story_id = found.storyId
            self.discussed_story_ids.add(found.storyId)
            return found
            
        if self.focus_story_id:
            for r in self.by_ref.values():
                if r.storyId == self.focus_story_id:
                    self.discussed_story_ids.add(r.storyId)
                    return r
                    
        raise errors.validation('No story is in focus. Ask the user which story they mean.')

TOOL_DESCRIPTIONS = {
    'get_user_context': "Returns the user's stored ambitions, profile facts, interests, things they asked to stop seeing and tracked stories.",
    'get_latest_developments': "Returns the user's current briefing items with what changed and why each matters to them, if a link is established.",
    'search_relevant_events': 'Searches stored stories by meaning. Use when the user asks about a topic or event.',
    'get_event_timeline': 'Returns how a story developed over time, or only what changed since the user last saw it.',
    'control_briefing': 'Controls the spoken briefing: stop it, skip the current story, or resume it. Call when the user asks for it.',
    'explain_relevance': 'Explains why a story matters to this user, based on stored ambitions and preferences. Says so when no link is supported.',
    'track_event': 'Starts tracking a story for the user. Call when the user asks to track or follow a story.',
    'untrack_event': 'Stops tracking a story.',
    'update_preference': 'Stores a topic the user wants to follow or stop seeing, permanently or for a number of days.',
    'submit_feedback': 'Records explicit feedback about a story: not_relevant, already_know, too_much, more_like_this, less_like_this, relevant, source_not_trusted or not_interested_temporarily.',
    'get_current_report': 'Returns the daily structured intelligence report, including executive summary, trajectory assessment, key developments, risks, opportunities, and recommended next actions.',
    'get_report_source': 'Looks up details for a cited source URL in the daily report.',
    'submit_voice_feedback': 'Interprets and saves spoken feedback or preference corrections into durable temporal memory using Nemotron.',
}

def is_tool_name(name: str) -> bool:
    return name in TOOL_DESCRIPTIONS

def get_gemini_tools() -> List[Any]:
    from google.genai import types
    declarations = [
        types.FunctionDeclaration(
            name='get_user_context',
            description="Returns the user's stored ambitions, profile facts, interests, things they asked to stop seeing and tracked stories.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "section": {
                        "type": "STRING",
                        "description": "Optional section to focus on: 'all', 'ambitions', 'profile', 'interests', 'tracked'"
                    }
                }
            }
        ),
        types.FunctionDeclaration(
            name='get_latest_developments',
            description="Returns the user's current briefing items with what changed and why each matters to them, if a link is established.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "limit": {"type": "INTEGER", "description": "Maximum number of items to return (default 5)"}
                }
            }
        ),
        types.FunctionDeclaration(
            name='search_relevant_events',
            description="Searches stored stories by meaning. Use when the user asks about a topic or event.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "query": {"type": "STRING", "description": "The search query or topic"},
                    "limit": {"type": "INTEGER", "description": "Maximum number of matches (default 3)"}
                },
                "required": ["query"]
            }
        ),
        types.FunctionDeclaration(
            name='get_event_timeline',
            description="Returns how a story developed over time, or only what changed since the user last saw it.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "story_ref": {"type": "INTEGER", "description": "The reference number of the story"},
                    "since_last_seen": {"type": "BOOLEAN", "description": "Only return updates since the user last viewed it"}
                },
                "required": ["story_ref"]
            }
        ),
        types.FunctionDeclaration(
            name='control_briefing',
            description="Controls the spoken briefing: stop it, skip the current story, or resume it. Call when the user asks for it.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "action": {"type": "STRING", "description": "One of: stop, skip, resume"}
                },
                "required": ["action"]
            }
        ),
        types.FunctionDeclaration(
            name='explain_relevance',
            description="Explains why a story matters to this user, based on stored ambitions and preferences. Says so when no link is supported.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "story_ref": {"type": "INTEGER", "description": "The reference number of the story"}
                },
                "required": ["story_ref"]
            }
        ),
        types.FunctionDeclaration(
            name='track_event',
            description="Starts tracking a story for the user. Call when the user asks to track or follow a story.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "story_ref": {"type": "INTEGER", "description": "The reference number of the story"}
                },
                "required": ["story_ref"]
            }
        ),
        types.FunctionDeclaration(
            name='untrack_event',
            description="Stops tracking a story.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "story_ref": {"type": "INTEGER", "description": "The reference number of the story"}
                },
                "required": ["story_ref"]
            }
        ),
        types.FunctionDeclaration(
            name='update_preference',
            description="Stores a topic the user wants to follow or stop seeing, permanently or for a number of days.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "topic": {"type": "STRING", "description": "Topic or keyword"},
                    "action": {"type": "STRING", "description": "'follow' to add interest, 'hide' to suppress"},
                    "duration": {"type": "STRING", "description": "'permanent' or 'temporary'"},
                    "days": {"type": "INTEGER", "description": "Number of days if temporary"}
                },
                "required": ["topic", "action"]
            }
        ),
        types.FunctionDeclaration(
            name='submit_feedback',
            description="Records explicit feedback about a story: not_relevant, already_know, too_much, more_like_this, less_like_this, relevant, source_not_trusted or not_interested_temporarily.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "story_ref": {"type": "INTEGER", "description": "The reference number of the story"},
                    "feedback_type": {"type": "STRING", "description": "Feedback type string"},
                    "reason": {"type": "STRING", "description": "Optional user reason"},
                    "days": {"type": "INTEGER", "description": "Optional days for temporary suppression"}
                },
                "required": ["story_ref", "feedback_type"]
            }
        ),
        types.FunctionDeclaration(
            name='get_current_report',
            description="Returns the daily structured intelligence report synthesized by NVIDIA Nemotron, including executive summary, trajectory assessment, key developments, risks, opportunities, and recommended next actions.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "section": {
                        "type": "STRING",
                        "description": "Optional section to retrieve: 'all', 'summary', 'developments', 'risks', 'opportunities', 'actions'"
                    }
                }
            }
        ),
        types.FunctionDeclaration(
            name='get_report_source',
            description="Looks up verified source details and publisher information for a cited source URL in the daily report.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "url": {"type": "STRING", "description": "The exact cited URL"}
                },
                "required": ["url"]
            }
        ),
        types.FunctionDeclaration(
            name='submit_voice_feedback',
            description="Interprets and saves spoken feedback or preference adjustments directly into durable temporal memory using NVIDIA Nemotron.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "feedback_text": {"type": "STRING", "description": "Natural language feedback or instruction spoken by the user"}
                },
                "required": ["feedback_text"]
            }
        )
    ]
    return [types.Tool(function_declarations=declarations)]

def parse_ref(value: Any) -> Optional[int]:
    if value is None or value == '':
        return None
    try:
        if isinstance(value, str):
            import re
            m = re.search(r'\d+', value)
            if m:
                parsed = int(m.group(0))
            else:
                return None
        else:
            parsed = int(value)
            
        if parsed < 1:
            raise ValueError()
        return parsed
    except Exception:
        raise errors.validation('story_ref must be the number of a listed story.')

def trim(text: Optional[str], max_len: int = 280) -> Optional[str]:
    if not text:
        return None
    return f"{text[:max_len-1]}…" if len(text) > max_len else text

def briefing_for_model(refs: SessionRefs, item: BriefingItem) -> Dict[str, Any]:
    ref = refs.register(StoryRef(storyId=item.storyId, developmentId=item.developmentId, title=item.storyTitle))
    return {
        "ref": ref,
        "story": item.storyTitle,
        "headline": item.headline,
        "whatChanged": trim(item.whatChanged),
        "continuity": item.continuity,
        "occurredAt": item.occurredAt[:10],
        "whyItMatters": item.whyItMatters,
        "couldChange": item.couldChange,
        "tracked": item.tracked,
        "sourceCount": len(item.sources)
    }

async def stored_factors(user_id: str, development_id: str) -> List[str]:
    row = unwrap_maybe(
        'coverage.factors',
        supabase.table('world_coverage').select('relevance_reasons')\
            .eq('user_id', user_id).eq('development_id', development_id).maybe_single().execute()
    )
    if not row or not row.get('relevance_reasons'):
        return []
    
    factors = row['relevance_reasons'].get('factors', [])
    return [f.get('detail') for f in factors if f.get('detail') and isinstance(f.get('detail'), str)]

async def latest_development_id(story_id: str) -> Optional[str]:
    row = unwrap_maybe(
        'developments.latest',
        supabase.table('developments').select('id')\
            .eq('story_id', story_id).order('occurred_at', desc=True).limit(1).maybe_single().execute()
    )
    return row['id'] if row else None

class ProfileContext(BaseModel):
    role: Optional[str] = None
    activity: Optional[str] = None
    depth: Optional[str] = None
    geographyFocus: Optional[str] = None
    categories: List[str] = []

class SessionContext(BaseModel):
    name: Optional[str] = None
    language: str = 'English'
    profile: ProfileContext
    history: Dict[str, Any] = {}
    identity: Dict[str, Any] = {}
    ambitions: List[Any] = []
    interests: List[Any] = []
    suppressions: List[Any] = []
    tracked: List[Any] = []
    briefing: List[BriefingItem] = []

async def load_session_context(user_id: str) -> SessionContext:
    user_row_res = supabase.table('users').select('full_name').eq('id', user_id).maybe_single().execute()
    profile_row_res = supabase.table('user_intelligence_profiles').select('prefered_language, role, activity, depth, geography_focus, categories').eq('user_id', user_id).maybe_single().execute()
    identity, ambitions, memory, tracked, briefing = await asyncio.gather(
        get_identity(user_id),
        list_ambitions(user_id),
        get_active_memory(user_id),
        list_tracked_stories(user_id),
        get_briefing_items(user_id, DOSSIER_ITEMS)
    )
    
    user_row = unwrap_maybe('users.name', user_row_res)
    profile_row = unwrap_maybe('profile.language', profile_row_res)
    
    history = await load_story_history(briefing.get('items', []))
    
    interests = [m for m in memory if m.memory_type in ('interest', 'temporary_interest') and m.source != 'inferred'][:8]
    suppressions = [m for m in memory if m.memory_type in ('suppression', 'temporary_suppression') and not (m.topic or '').startswith(('story:', 'development:', 'source:'))][:6]
    
    profile_ctx = ProfileContext(
        role=profile_row.get('role') if profile_row else None,
        activity=profile_row.get('activity') if profile_row else None,
        depth=profile_row.get('depth') if profile_row else None,
        geographyFocus=profile_row.get('geography_focus') if profile_row else None,
        categories=profile_row.get('categories') if profile_row and profile_row.get('categories') else []
    )

    return SessionContext(
        name=user_row.get('full_name') if user_row else None,
        language=profile_row.get('prefered_language') if profile_row and profile_row.get('prefered_language') else 'English',
        profile=profile_ctx,
        history=history,
        identity=identity if isinstance(identity, dict) else {},
        ambitions=[a for a in ambitions if getattr(a, 'status', None) == 'active'][:3],
        interests=interests,
        suppressions=suppressions,
        tracked=tracked[:5],
        briefing=briefing.get('items', [])
    )

async def execute_tool(user_id: str, refs: SessionRefs, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    if not is_tool_name(name):
        raise errors.validation(f"Unknown tool {name}.")
        
    log.info('tool call', {"userId": user_id, "name": name})
    
    refs.tool_calls += 1
    if refs.tool_calls > env.VOICE_MAX_TOOL_CALLS:
        raise errors.validation('This voice session has used all of its tool calls.')
        
    user_said = args.get('user_said')
    if is_changing_tool(name) and not heard_request(refs.heard() if refs.heard else '', user_said):
        log.warn('tool refused: the user did not ask for it', {"userId": user_id, "name": name})
        return {"ok": False, "note": "The user did not ask for that in their recent words, so nothing was changed. Do not say it was done."}
        
    if name == 'get_user_context':
        ctx = await load_session_context(user_id)
        return {
            "name": ctx.name,
            "profile": ctx.identity,
            "ambitions": [{"title": a.title, "horizon": a.horizon, "geography": a.geography, "priority": a.priority} for a in ctx.ambitions],
            "followedTopics": [i.topic for i in ctx.interests],
            "hiddenTopics": [s.topic for s in ctx.suppressions],
            "trackedStories": [{"ref": refs.register(StoryRef(storyId=t.storyId, developmentId=None, title=t.title)), "title": t.title, "hasUpdates": t.hasUpdates} for t in ctx.tracked]
        }
        
    elif name == 'get_latest_developments':
        limit = args.get('limit', 5)
        res = await get_briefing_items(user_id, limit)
        items = res.get('items', [])
        if not items:
            return {"developments": [], "note": "There are no developments in the briefing yet."}
        return {"developments": [briefing_for_model(refs, i) for i in items]}
        
    elif name == 'search_relevant_events':
        query = args.get('query')
        limit = args.get('limit', 3)
        vector = await embed_text(query, 'query')
        matches = await rpc_rows('match_stories', {
            "p_embedding": vector,
            "p_limit": limit,
            "p_since": None
        })
        
        close = [m for m in matches if m.get('similarity', 0) >= 0.55]
        if not close:
            return {"results": [], "note": "No stored story matches that."}
            
        import asyncio
        items_res, latest_per_story = await asyncio.gather(
            get_briefing_items(user_id, 50),
            asyncio.gather(*[load_developments({"storyId": story['id'], "limit": 1}) for story in close])
        )
        
        items = items_res.get('items', [])
        results = []
        for index, story in enumerate(close):
            latest_list = latest_per_story[index]
            latest = latest_list[0] if latest_list else None
            in_briefing = next((i for i in items if i.storyId == story['id']), None)
            ref = refs.register(StoryRef(storyId=story['id'], developmentId=latest['id'] if latest else None, title=story['title']))
            
            results.append({
                "ref": ref,
                "story": story['title'],
                "headline": latest['headline'] if latest else None,
                "whatChanged": trim(latest['what_changed']) if latest else None,
                "occurredAt": latest['occurred_at'][:10] if latest else None,
                "whyItMatters": in_briefing.whyItMatters if in_briefing else None,
                "couldChange": in_briefing.couldChange if in_briefing else None
            })
            
        refs.focus_story_id = close[0]['id']
        return {"results": results}
        
    elif name == 'get_event_timeline':
        story_ref = parse_ref(args.get('story_ref'))
        target = refs.resolve(story_ref)
        timeline = await get_timeline(user_id, target.storyId)
        
        since_last = bool(args.get('since_last_seen')) and timeline.changeSinceLastSeen is not None
        developments = timeline.changeSinceLastSeen.developments if since_last else timeline.developments
        
        if since_last and timeline.tracked:
            await mark_seen(user_id, target.storyId)
            
        return {
            "story": timeline.story.title,
            "tracked": timeline.tracked,
            "scope": f"since you last saw this ({timeline.changeSinceLastSeen.since[:10]})" if since_last else "full history",
            "developments": [{
                "date": d.occurredAt[:10],
                "headline": d.headline,
                "whatChanged": trim(d.whatChanged),
                "continuity": d.continuity,
                "sources": len(d.sources)
            } for d in developments[-6:]],
            "note": "Nothing new since the user last saw this story." if not developments else None
        }
        
    elif name == 'explain_relevance':
        story_ref = parse_ref(args.get('story_ref'))
        target = refs.resolve(story_ref)
        dev_id = target.developmentId or await latest_development_id(target.storyId)
        
        if not dev_id:
            raise errors.not_found('Development')
            
        items_res = await get_briefing_items(user_id, 50)
        items = items_res.get('items', [])
        stored = next((i for i in items if i.developmentId == dev_id), None)
        
        if stored:
            factors = await stored_factors(user_id, dev_id)
            return {
                "story": target.title,
                "linkEstablished": stored.relevanceBasis is not None and stored.relevanceBasis != 'general',
                "basis": stored.relevanceBasis,
                "ambition": stored.ambitionTitle,
                "whyItMatters": stored.whyItMatters,
                "couldChange": stored.couldChange,
                "factors": factors,
                "note": None if stored.whyItMatters else "No link to the ambitions or preferences on record could be established."
            }
            
        devs = await load_developments({"ids": [dev_id], "limit": 1})
        if not devs:
            raise errors.not_found('Development')
        dev = devs[0]
        ctx = await load_user_context(user_id)
        evaluated = await evaluate_development(ctx, dev)
        d = evaluated.decision
        
        if getattr(d, 'suppressed', False):
            return {"story": target.title, "hidden": True, "reason": getattr(d, 'suppressedReason', None)}
            
        return {
            "story": target.title,
            "linkEstablished": d.basis is not None and d.basis != 'general',
            "basis": d.basis,
            "ambition": evaluated.ambitionTitle,
            "whyItMatters": d.whyItMatters,
            "couldChange": d.couldChange,
            "factors": [f.detail for f in d.factors],
            "note": None if d.whyItMatters else "No link to the ambitions or preferences on record could be established."
        }
        
    elif name == 'control_briefing':
        action = args.get('action')
        if not refs.briefing:
            return {"ok": False, "note": "There is no spoken briefing running."}
            
        if action == 'stop':
            refs.briefing.stop()
        elif action == 'skip_story':
            refs.briefing.skip_story()
        else:
            refs.briefing.resume()
            
        return {"ok": True, "action": action}
        
    elif name == 'track_event':
        story_ref = parse_ref(args.get('story_ref'))
        target = refs.resolve(story_ref)
        await track_story(user_id, target.storyId, True)
        return {"ok": True, "tracking": target.title}
        
    elif name == 'untrack_event':
        story_ref = parse_ref(args.get('story_ref'))
        target = refs.resolve(story_ref)
        await untrack_story(user_id, target.storyId, True)
        return {"ok": True, "untracked": target.title}
        
    elif name == 'update_preference':
        topic = args.get('topic')
        action = args.get('action')
        temporary = args.get('duration') == 'temporary'
        days = args.get('days')
        
        if temporary and not days:
            raise errors.validation('days is required for a temporary preference.')
            
        if action == 'follow':
            mem_type = 'temporary_interest' if temporary else 'interest'
        else:
            mem_type = 'temporary_suppression' if temporary else 'suppression'
            
        await remember(MemoryInput(
            userId=user_id,
            type=mem_type,
            topic=topic,
            strength=0.9,
            confidence=1.0,
            source='explicit',
            validUntil=days_from_now(days) if temporary and days else None
        ))
        await refresh_briefing(user_id, RefreshOptions(background=True))
        return {"ok": True, "saved": f"{'Following' if action == 'follow' else 'Hiding'} \"{topic}\"{f' for {days} days' if temporary else ' permanently'}"}
        
    elif name == 'submit_feedback':
        story_ref = parse_ref(args.get('story_ref'))
        target = refs.resolve(story_ref)
        fb_type = args.get('feedback_type')
        
        dev_id = None
        if fb_type == 'already_know':
            dev_id = target.developmentId or await latest_development_id(target.storyId)
            
        fb_input = FeedbackInput(
            storyId=target.storyId,
            developmentId=dev_id,
            feedbackType=fb_type,
            reason=args.get('reason'),
            days=args.get('days')
        )
        
        result = await apply_feedback(user_id, fb_input, background=True)
        return {"ok": True, "story": target.title, "effect": result.get('effect')}
        
    elif name == 'get_current_report':
        report_data = await get_latest_report(user_id)
        if not report_data:
            return {"ok": False, "note": "No daily report is available yet for today."}
        return {
            "ok": True,
            "date": report_data.get("reportDate"),
            "executiveSummary": report_data.get("executiveSummary"),
            "trajectoryAssessment": report_data.get("trajectoryAssessment"),
            "opportunities": report_data.get("opportunities"),
            "risks": report_data.get("risks"),
            "recommendedActions": report_data.get("recommendedActions"),
            "sourcesCount": len(report_data.get("sources") or []),
            "modelUsed": report_data.get("modelUsed")
        }

    elif name == 'get_report_source':
        url = args.get('url')
        report_data = await get_latest_report(user_id)
        sources = report_data.get("sources") or [] if report_data else []
        found = next((s for s in sources if s.get('url') == url), None)
        if found:
            return {"ok": True, "source": found}
        return {"ok": False, "note": "Source URL not found in today's report."}

    elif name == 'submit_voice_feedback':
        feedback_text = args.get('feedback_text') or args.get('feedback')
        if not feedback_text:
            raise errors.validation('feedback_text is required.')
        res = await interpret_and_apply_user_feedback(user_id, feedback_text, source="voice")
        return {"ok": True, "message": res.get("message"), "operation": res.get("interpretation", {}).get("operation")}
        
    return {}
