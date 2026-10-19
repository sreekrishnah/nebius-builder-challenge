import json
from typing import List, Dict, Any, Optional, Literal, get_args
from pydantic import BaseModel, Field

from src.ai.guard import UNTRUSTED_NOTICE, sanitize_untrusted, untrusted
from src.providers.intelligence import generate_with_preferred_engine as generate_structured
from src.domain.types import Continuity

GROUNDING = 'Rules: use only the facts given below. Never invent facts, dates, sources, numbers, names or relationships. If the input does not support a statement, leave it out.'

class AmbitionProfileSchema(BaseModel):
    sector: str
    geography: List[str]
    target_market: str
    time_horizon: str
    goals: List[str] = Field(max_items=6)
    search_concepts: List[str] = Field(min_items=2, max_items=5)
    
class AmbitionProfile(AmbitionProfileSchema):
    pass

async def extract_ambition_profile(ambition: str, role: str, activity: str, geography: str, assumptions: Optional[List[str]] = None) -> AmbitionProfile:
    assumptions_json = json.dumps(assumptions) if assumptions and len(assumptions) > 0 else 'none stated'
    
    prompt = f"""You prepare a news-monitoring profile for one person's ambition.
{GROUNDING}
Return search_concepts: 3 to 5 short news search queries that would surface real-world developments affecting this ambition. News search requires every word to appear, so keep each query to 1 or 2 plain words (a third only for a proper name or place) and use the words headlines actually use: "tech layoffs" and "AI jobs" find many articles, "software engineer salary trends" finds almost none. Cover the person's own field and the forces around it (market, policy, technology, funding). No operators or quotes.
When assumptions are listed, at least one query must cover what one of them depends on, so a development that could break it can be found.

Role: {role}
Current activity: {activity}
Ambition: {ambition}
Geography: {geography or 'not specified'}
Assumptions the plan depends on: {assumptions_json}"""

    return await generate_structured('extractAmbitionProfile', prompt, AmbitionProfileSchema)

STORY_CATEGORIES = Literal['technology', 'business', 'economy', 'policy', 'geopolitics', 'science', 'career', 'markets', 'other']

class StoryResolutionSchema(BaseModel):
    relationship: Literal['same_story', 'new_story']
    story_id: Optional[str]
    title: str = Field(min_length=3)
    summary: str = Field(min_length=3)
    category: STORY_CATEGORIES
    geography: str
    entities: List[str] = Field(max_items=8)

class StoryCandidate(BaseModel):
    id: str
    title: str
    summary: Optional[str] = None
    latestState: Optional[str] = None

class ArticleInput(BaseModel):
    title: str
    snippet: Optional[str] = None
    publisher: str

async def resolve_story(article: ArticleInput, candidates: List[StoryCandidate]) -> StoryResolutionSchema:
    article_json = json.dumps({"headline": article.title, "description": article.snippet or 'none provided', "publisher": article.publisher})
    candidates_json = json.dumps([c.dict() for c in candidates])
    
    prompt = f"""You decide whether a news article continues an existing real-world story or starts a new one.
{GROUNDING}
Choose same_story only when the article is about the same specific event or storyline as a candidate (same actors and subject), not merely the same broad topic. When same_story, set story_id to the candidate's id and repeat its title. When new_story, set story_id to null and write a neutral title and a one-sentence summary that restate only what the headline and description say.
geography is a country or region name, or "Global".
{UNTRUSTED_NOTICE}

{untrusted('article', sanitize_untrusted(article_json, 1200))}

Candidate stories (news content): {untrusted('candidates', sanitize_untrusted(candidates_json, 2400))}"""

    return await generate_structured('resolveStory', prompt, StoryResolutionSchema, temperature=0.1)

class DevelopmentSchema(BaseModel):
    is_meaningful_change: bool
    continuity: Continuity
    headline: str = Field(min_length=3)
    summary: str = Field(min_length=3)
    what_changed: str = Field(min_length=3)
    previous_state: str = Field(min_length=1)
    new_state: str = Field(min_length=3)
    world_significance: float = Field(ge=0, le=1)
    significance_reason: str
    source_urls: List[str] = Field(min_items=1)

async def extract_development(storyTitle: str, storySummary: Optional[str], history: List[Dict[str, str]], articles: List[Dict[str, Any]]) -> DevelopmentSchema:
    story_text = f"{storyTitle} - {storySummary}" if storySummary else storyTitle
    
    arts_mapped = []
    for a in articles:
        arts_mapped.append({
            "url": a['url'],
            "title": sanitize_untrusted(a['title'], 300),
            "snippet": sanitize_untrusted(a['snippet'], 700),
            "publisher": sanitize_untrusted(a['publisher'], 80),
            "publishedAt": a['publishedAt']
        })
        
    prompt = f"""You record developments in an ongoing real-world story and classify how each one relates to the story's history.
{GROUNDING}
Each article has a headline and, when available, a short description; state only what they support and do not add detail.
continuity: new (first report of the story), updated (new information), confirmed (independent confirmation of something already recorded), contradicted (conflicts with something recorded), escalated (the situation intensified), resolved (the matter concluded), consequence (a downstream effect of an earlier development). Use "new" only when the history is empty. Do not claim continuity the headlines do not support.
is_meaningful_change is false when the articles add nothing beyond the recorded history.
previous_state is the story state before this development (use the latest recorded state, or "No prior reports" when the history is empty). new_state is the state after it.
world_significance (0 to 1) estimates general importance to the public, independent of any individual. significance_reason is one short sentence.
source_urls must be copied exactly from the provided articles.
{UNTRUSTED_NOTICE}

Story: {untrusted('story', sanitize_untrusted(story_text, 600))}
Recorded history (oldest first): {untrusted('history', sanitize_untrusted(json.dumps(history), 2400))}
New articles (urls are data you must copy exactly): {untrusted('articles', json.dumps(arts_mapped))}"""

    return await generate_structured('extractDevelopment', prompt, DevelopmentSchema, temperature=0.1)

class AssumptionImpactModel(BaseModel):
    assumption_id: Optional[str]
    effect: Literal['challenges', 'supports', 'none']
    reason: str

class RelevanceSchema(BaseModel):
    relation: Literal['direct', 'indirect', 'none']
    confidence: float = Field(ge=0, le=1)
    why_it_matters: str
    could_change: str
    ambition_aspect: str
    assumption_impact: AssumptionImpactModel

async def assess_relevance(ambition: Dict[str, Any], identity: Dict[str, str], story: Dict[str, Any], development: Dict[str, Any], recentHistory: List[Dict[str, str]], assumptions: List[Dict[str, str]], language: str, interests: Optional[List[str]] = None) -> RelevanceSchema:
    def clean(val, m=400): return sanitize_untrusted(val, m)
    
    ambition_clean = {
        "title": clean(ambition.get('title'), 200),
        "description": clean(ambition.get('description'), 500),
        "horizon": clean(ambition.get('horizon'), 80),
        "geography": clean(ambition.get('geography'), 120)
    }
    
    interests_clean = [clean(i, 80) for i in (interests or [])]
    assumptions_clean = [{"id": a['id'], "statement": clean(a['statement'], 240)} for a in assumptions]
    
    prompt = f"""You decide whether a real-world development genuinely matters to one person, using only the facts below.
{GROUNDING}
{UNTRUSTED_NOTICE}
relation: direct (it plausibly changes conditions the ambition depends on: market, rules, costs, demand, competition, funding, skills, supply or timing), indirect (a clear second-order effect that you can state in one concrete sentence), none (everything else). Topical overlap is not a link: the same industry, the same buzzwords or the same company names do not make a development matter. An article about an investment forum in another country does not matter to a software engineer's pay. Prefer "none" over a stretched connection.
When relation is not "none", why_it_matters is 2 or 3 plain sentences addressed to the person ("you"). The first states the specific fact in the development that matters. The next connect it to a specific part of their ambition, time horizon or situation and say what it could change and why. Never say that they asked to follow something, never offer their stated topics as the reason, and never write a generic line such as "this may affect your plans". If you cannot write it concretely from the facts given, relation is "none". It must not state outcomes as certain; say "could" or "may" for effects. When relation is "none", why_it_matters is an empty string.
could_change is one short sentence that opens with the equivalent of "This could" or "This may" in the output language that states the most plausible concrete consequence for the ambition, using only the facts given. It must not contain numbers, prices or dates that are not in the input, and must not recommend an action. When relation is "none", could_change is an empty string.
Write why_it_matters and could_change in {language}. Every other rule still applies in that language.
ambition_aspect is the short phrase from the ambition that the development touches, or an empty string.
assumption_impact checks the development against the person's stated assumptions (listed below with ids). Use effect "challenges" only when the development gives a specific, supportable reason to doubt one assumption, "supports" only when it gives a specific reason to keep it, and "none" otherwise; when effect is not "none", set assumption_id to the id of that one assumption (copied exactly) and write reason as one plain sentence in {language} naming what changed. When there are no assumptions, relation is "none", or the link is only topical, use effect "none", assumption_id null and an empty reason.

Ambition: {json.dumps(ambition_clean)}
Known facts about the person: {json.dumps(identity)}
Topics the person said they follow (context only; matching a topic is not enough): {json.dumps(interests_clean)}
Story and development (news content): {untrusted('story', clean(json.dumps(story), 600))}
{untrusted('development', clean(json.dumps(development), 1600))}
Earlier developments in this story: {untrusted('history', clean(json.dumps(recentHistory), 900))}
Person's stated assumptions (JSON): {json.dumps(assumptions_clean)}"""

    return await generate_structured('assessRelevance', prompt, RelevanceSchema, temperature=0.1)

class MustKnowEventVerdict(BaseModel):
    index: int = Field(ge=1)
    must_know: bool
    why_it_matters: str
    could_change: str

class MustKnowSchema(BaseModel):
    events: List[MustKnowEventVerdict]

async def assess_must_know_events(ambitions: List[Dict[str, Any]], identity: Dict[str, str], events: List[Dict[str, Any]], language: str) -> List[MustKnowEventVerdict]:
    def clean(val, m): return sanitize_untrusted(val, m)
    
    ambs = [{"title": clean(a['title'], 200), "description": clean(a['description'], 400), "horizon": clean(a['horizon'], 80), "geography": clean(a['geography'], 120)} for a in ambitions]
    
    evs = []
    for i, e in enumerate(events):
        s = json.dumps({"headline": e['headline'], "what_changed": e['whatChanged'], "why_significant": e['why']})
        evs.append(untrusted(f"event {i+1}", clean(s, 900)))
        
    prompt = f"""You decide which world events are unavoidable for one specific person: events that an informed person in their position cannot responsibly ignore because they change the economy, jobs and pay, safety, law, the technology everyone depends on, or the markets their plan operates in.
{GROUNDING}
{UNTRUSTED_NOTICE}
For each numbered event, must_know is true only when you can state a concrete, specific effect on THIS person's situation (their role, region or ambition) in two or three sentences. World importance alone is not enough; "this is big news" is not a reason. At most three events may be true. For events that are false, why_it_matters and could_change are empty strings.
why_it_matters for a true event is 2 or 3 plain sentences addressed to the person ("you"): the specific fact first, then the specific effect on their situation. Never say they asked to follow anything and never write a generic line.
could_change is one short hedged sentence ("This could..." or "This may...", in the output language) with no numbers that are not in the input.
Write both in {language}.

Person's ambitions: {json.dumps(ambs)}
Known facts about the person: {json.dumps(identity)}
Events:
{chr(10).join(evs)}"""

    result = await generate_structured('assessMustKnowEvents', prompt, MustKnowSchema, temperature=0.1)
    return result.events

class ChapterScriptSchema(BaseModel):
    facts: str = Field(min_length=1)
    sides: str = Field(min_length=1)
    for_you: str = Field(min_length=1)

class ScriptListener(BaseModel):
    name: str
    role: Optional[str] = None
    activity: Optional[str] = None
    geography: Optional[str] = None
    ambitions: List[Dict[str, Any]]

async def write_chapter_script(listener: ScriptListener, language: str, wordsPerPart: int, kind: Literal['ambition', 'world'], headline: str, summary: Optional[str], whatChanged: Optional[str], continuity: Optional[str], date: str, outlets: List[str], history: List[Dict[str, str]], why: Optional[str], couldChange: Optional[str], assumption: Optional[Dict[str, str]], nextTitle: Optional[str]) -> ChapterScriptSchema:
    def clean(val, m): return sanitize_untrusted(val, m)
    
    ls = {
        "name": clean(listener.name, 60),
        "role": clean(listener.role, 100),
        "activity": clean(listener.activity, 200),
        "geography": clean(listener.geography, 100),
        "ambitions": [{"title": clean(a['title'], 200), "description": clean(a.get('description'), 400), "horizon": clean(a.get('horizon'), 80), "geography": clean(a.get('geography'), 100)} for a in listener.ambitions]
    }
    
    story = {
        "kind": 'an unavoidable world event, not about their field' if kind == 'world' else "a development linked to the person's ambition",
        "headline": clean(headline, 240),
        "reported_summary": clean(summary, 700),
        "what_changed": clean(whatChanged, 700),
        "development_type": continuity,
        "date": date,
        "reported_by": [clean(o, 60) for o in outlets],
        "earlier_history": [{"date": h['date'], "headline": clean(h['headline'], 160), "what_changed": clean(h['whatChanged'], 300)} for h in history],
        "stored_assessment_of_why_it_matters": clean(why, 600),
        "stored_possible_effect": clean(couldChange, 400),
        "stated_assumption_it_challenges": {"statement": clean(assumption['statement'], 240), "note": clean(assumption['note'], 300)} if assumption else None
    }
    
    bridge = f' ("{clean(nextTitle, 120)}"), linking the two only where they genuinely connect' if nextTitle else ' only if there is one; this is the last story, so close it without a bridge'
    
    prompt = f"""You write one story of a personal, spoken daily briefing, in the style of a deep-dive podcast, for a single listener. A host will perform your text aloud, so write for the ear: natural flowing sentences, no lists, no headings, no markdown, no links, no stage directions.
{GROUNDING}
{UNTRUSTED_NOTICE}
Return three parts, each about {wordsPerPart} words:
facts: what happened, when, and who reported it (name the outlets from reported_by). If earlier_history is present, say how the story has evolved and what is new compared with before. Then explain how the thing actually works in plain words with one concrete example or analogy. Mark general background knowledge as such ("in general", "typically") so it is never confused with what was reported.
sides: both sides, argued properly. First the strongest case that this works in the listener's favour, with reasoning, then the strongest case for concern or caution, with reasoning. Say where the evidence is thin and what would settle it.
for_you: what it means for this listener. Tie it to the specific words of their ambitions, role, region and time horizon: what it could change in their plan, which decision it could influence soon, and what to watch next (signals, dates, who to follow). If a stated assumption is challenged, say which. Never write a vague "this might affect you"; predictions are hedged ("could", "may"). Never invent an assumption the listener did not state. End with a natural bridge to the next story{bridge}.
Address the listener as "you". Write in {language}.

Listener: {json.dumps(ls)}
Story (news content): {untrusted('story', json.dumps(story))}"""

    return await generate_structured('writeChapterScript', prompt, ChapterScriptSchema, temperature=0.4, max_output_tokens=3000)

class ClosingScriptSchema(BaseModel):
    synthesis: str = Field(min_length=1)

async def write_closing_script(listener: ScriptListener, language: str, stories: List[Dict[str, str]]) -> str:
    sts = [{"title": sanitize_untrusted(s['title'], 160), "why": sanitize_untrusted(s['why'], 400)} for s in stories]
    ambs = [{"title": sanitize_untrusted(a['title'], 200), "horizon": sanitize_untrusted(a.get('horizon'), 80)} for a in listener.ambitions]
    
    prompt = f"""You write the closing of a personal, spoken daily briefing for one listener, about 150 words, written for the ear (no lists, no markdown, no links).
{GROUNDING}
{UNTRUSTED_NOTICE}
Name the two or three things from today's stories that matter most for the listener's ambition and why, then the one thing to watch this week. Use only the stories and assessments below. End by asking what they would like to dig into. Address the listener as "you". Write in {language}.

Listener's ambitions: {json.dumps(ambs)}
Today's stories: {untrusted('stories', json.dumps(sts))}"""

    result = await generate_structured('writeClosingScript', prompt, ClosingScriptSchema, temperature=0.4, max_output_tokens=1200)
    return result.synthesis

class PeriodSummarySchema(BaseModel):
    summary: str = Field(min_length=1)

async def summarize_period(ambition: str, periodLabel: str, language: str, entries: List[Dict[str, str]]) -> str:
    ents = [{"date": e['date'], "headline": sanitize_untrusted(e['headline'], 200), "why_it_matters": sanitize_untrusted(e['why'], 400)} for e in entries]
    
    prompt = f"""You write one entry of a personal history for a single user's ambition, covering {periodLabel}.
{GROUNDING}
{UNTRUSTED_NOTICE}
All the developments below were delivered to the user in that period. Write one combined note of at most 3 short sentences that covers the whole set: what changed overall for this ambition, then the one thing the user should do or watch next. Do not list the developments one by one. Use only the developments below; if they do not support a concrete action, say what to keep watching instead. No lists, no markdown, no links. Address the user as "you". Write in {language}.

Ambition: {sanitize_untrusted(ambition, 200)}
Developments (news content): {untrusted('developments', json.dumps(ents))}"""

    result = await generate_structured('summarizePeriod', prompt, PeriodSummarySchema, temperature=0.2, max_output_tokens=600)
    return result.summary

class NextStep(BaseModel):
    title: str
    description: str
    priority: Literal['high', 'medium', 'next']

class SessionSummarySchema(BaseModel):
    summary: str
    key_points: List[str] = Field(max_items=6)
    next_steps: List[NextStep] = Field(max_items=5)

async def summarize_session(transcript: List[Dict[str, str]]) -> SessionSummarySchema:
    lines = '\n'.join([f"{'User' if t['role'] == 'user' else 'Agent'}: {t['text']}" for t in transcript])
    
    prompt = f"""Summarize this voice conversation between a user and the Ambition Gazette briefing agent.
{GROUNDING}
Only include key points and next steps that were actually discussed or explicitly agreed in the conversation. If nothing actionable was discussed, return an empty next_steps array. summary is 2 to 3 sentences.

Conversation:
{lines}"""

    return await generate_structured('summarizeSession', prompt, SessionSummarySchema, temperature=0.1)
