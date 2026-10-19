from typing import List, Optional, Dict, Tuple, Literal
from pydantic import BaseModel
from datetime import datetime

from src.domain.types import Attention, MemoryRow, RelevanceBasis
from src.domain.attention import AssumptionImpact, effective_impact, decide_attention, DecideAttentionInput

THRESHOLDS = {
    'memoryMatch': 0.68,
    'ambitionCandidate': 0.55,
    'ambitionConfidenceMin': 0.5,
    'generalSignificance': 0.75,
    'minSubstringLength': 4,
}

class DevelopmentFacts(BaseModel):
    developmentId: str
    storyId: str
    storyTitle: str
    geography: Optional[str] = None
    entities: List[str]
    text: str
    worldSignificance: float
    occurredAt: str
    publishers: List[str]

class AmbitionCandidate(BaseModel):
    id: str
    title: str
    priority: int
    similarity: float

class AmbitionAssessment(BaseModel):
    relation: Literal['direct', 'indirect', 'none']
    confidence: float
    whyItMatters: str
    couldChange: str
    assumptionImpact: Optional[AssumptionImpact] = None

class RelevanceInput(BaseModel):
    development: DevelopmentFacts
    memory: List[MemoryRow]
    memorySimilarity: Dict[str, float]
    trackedStory: bool
    ambition: Optional[AmbitionCandidate] = None
    assessment: Optional[AmbitionAssessment] = None
    identity: Dict[str, str]
    now: datetime

class Factor(BaseModel):
    rule: int
    basis: RelevanceBasis
    effect: Literal['boost', 'suppress']
    detail: str

RelevanceFactor = Factor

class RelevanceDecision(BaseModel):
    show: bool
    suppressed: bool
    suppressedReason: Optional[str] = None
    suppressedMemoryId: Optional[str] = None
    basis: Optional[RelevanceBasis] = None
    personalRelevance: float
    factors: List[Factor]
    ambitionId: Optional[str] = None
    whyItMatters: Optional[str] = None
    couldChange: Optional[str] = None
    attention: Optional[Attention] = None
    assumption: Optional[AssumptionImpact] = None
    needsAmbitionAssessment: bool

BASIS_BY_RULE: Dict[int, RelevanceBasis] = {
    1: 'temporary_interest',
    2: 'preference',
    3: 'ambition',
    4: 'tracked',
    5: 'interest',
    6: 'recent_interest',
    7: 'general',
}

def includes_topic(haystack: str, topic: str) -> bool:
    return len(topic) >= THRESHOLDS['minSubstringLength'] and topic in haystack

def memory_matches(row: MemoryRow, dev: DevelopmentFacts, similarity: Dict[str, float]) -> bool:
    topic = row.topic or ''
    if topic.startswith('story:'):
        return topic == f"story:{dev.storyId}"
    if topic.startswith('development:'):
        return topic == f"development:{dev.developmentId}"
    if topic.startswith('source:'):
        domain = topic[len('source:'):]
        return len(dev.publishers) > 0 and all(p.lower() == domain for p in dev.publishers)
    
    haystack = f"{dev.storyTitle} {dev.text} {' '.join(dev.entities)}".lower()
    if includes_topic(haystack, topic.lower()):
        return True
    return similarity.get(row.id, 0) >= THRESHOLDS['memoryMatch']

def rule_for(row: MemoryRow) -> Optional[Tuple[int, Literal['boost', 'suppress']]]:
    if row.memory_type == 'temporary_suppression':
        return (1, 'suppress')
    if row.memory_type == 'temporary_interest':
        return (1, 'boost')
    if row.memory_type == 'suppression':
        return (2, 'suppress')
    if row.memory_type == 'interest':
        if row.source == 'inferred':
            return None
        return (2, 'boost')
    if row.memory_type == 'tracked_entity':
        return (4, 'boost')
    return None

def recency_factor(occurred_at: str, now: datetime) -> float:
    # Use UTC explicitly since dates might come with or without tz
    occurred = datetime.fromisoformat(occurred_at.replace('Z', '+00:00'))
    age_days = (now.timestamp() - occurred.timestamp()) / 86400
    if age_days < 2: return 1.0
    if age_days < 7: return 0.92
    if age_days < 30: return 0.8
    return 0.65

def priority_weight(priority: int) -> float:
    return 1.0 if priority == 1 else (0.92 if priority == 2 else 0.84)

def describe(row: MemoryRow) -> str:
    topic = row.topic or row.content
    if topic.startswith('story:') or topic.startswith('development:'):
        return 'a story or update you marked'
    if topic.startswith('source:'):
        return f"the source {topic[7:]}"
    return f'"{topic}"'

def location(identity: Dict[str, str], dev: DevelopmentFacts) -> bool:
    place = identity.get('location', '').lower()
    geo = (dev.geography or '').lower()
    if not place or not geo:
        return False
    return place in geo or geo in place

from typing import Literal
def decide_relevance(input_data: RelevanceInput) -> RelevanceDecision:
    dev = input_data.development
    memory = input_data.memory
    memory_similarity = input_data.memorySimilarity
    tracked_story = input_data.trackedStory
    ambition = input_data.ambition
    assessment = input_data.assessment
    identity = input_data.identity
    now = input_data.now

    factors: List[Factor] = []
    suppress_rule: Optional[int] = None
    suppressed_reason: Optional[str] = None
    suppressed_memory_id: Optional[str] = None
    
    # rule, detail
    followed: Optional[Tuple[int, str]] = None

    for row in memory:
        mapped = rule_for(row)
        if not mapped or not memory_matches(row, dev, memory_similarity):
            continue
            
        rule, effect = mapped
        basis = BASIS_BY_RULE[rule]
        detail = f"You asked to stop seeing {describe(row)}" if effect == 'suppress' else f"You asked to follow {describe(row)}"
        factors.append(Factor(rule=rule, basis=basis, effect=effect, detail=detail))

        if effect == 'suppress':
            if suppress_rule is None or rule < suppress_rule:
                suppress_rule = rule
                suppressed_reason = detail
                suppressed_memory_id = row.id
            continue
            
        if rule == 4:
            continue
            
        if followed is None or rule < followed[0]:
            followed = (rule, detail)

    if suppress_rule is not None and (followed is None or suppress_rule <= followed[0]):
        return RelevanceDecision(
            show=False,
            suppressed=True,
            suppressedReason=suppressed_reason,
            suppressedMemoryId=suppressed_memory_id,
            basis=None,
            personalRelevance=0,
            factors=factors,
            ambitionId=None,
            whyItMatters=None,
            couldChange=None,
            attention=None,
            assumption=None,
            needsAmbitionAssessment=False,
        )

    if tracked_story:
        factors.append(Factor(rule=4, basis='tracked', effect='boost', detail='You are tracking this story'))

    ambition_plausible = ambition is not None and ambition.similarity >= THRESHOLDS['ambitionCandidate']
    eligible = ambition_plausible or followed is not None or tracked_story
    
    needs_ambition_assessment = False
    ambition_id: Optional[str] = None
    ambition_why: Optional[str] = None
    could_change: Optional[str] = None
    assumption: Optional[AssumptionImpact] = None
    link_score: Optional[float] = None

    if eligible and ambition:
        if not assessment:
            needs_ambition_assessment = True
        elif (
            assessment.relation != 'none' and
            assessment.confidence >= THRESHOLDS['ambitionConfidenceMin'] and
            len(assessment.whyItMatters.strip()) > 0
        ):
            link_score = 0.5 + 0.4 * assessment.confidence * priority_weight(ambition.priority) + (0.05 if followed else 0)
            factors.append(Factor(
                rule=3,
                basis='ambition',
                effect='boost',
                detail=f'Linked to your ambition "{ambition.title}" ({assessment.relation})'
            ))
            ambition_id = ambition.id
            ambition_why = assessment.whyItMatters.strip()
            could_change = assessment.couldChange.strip() or None
            assumption = effective_impact(assessment.assumptionImpact, assessment.relation, assessment.confidence)

    grounds: List[Dict[str, Any]] = []
    if link_score is not None and ambition_why:
        grounds.append({"rule": followed[0] if followed else 3, "score": link_score, "why": ambition_why})
    if tracked_story:
        grounds.append({"rule": 4, "score": 0.8, "why": ambition_why or 'You are tracking this story, and this is its latest development.'})

    if not grounds:
        return RelevanceDecision(
            show=False,
            suppressed=False,
            suppressedReason=None,
            suppressedMemoryId=None,
            basis=None,
            personalRelevance=0,
            factors=factors,
            ambitionId=None,
            whyItMatters=None,
            couldChange=None,
            attention=None,
            assumption=None,
            needsAmbitionAssessment=needs_ambition_assessment,
        )

    top = min(grounds, key=lambda g: (g['rule'], -g['score']))
    score = max(g['score'] for g in grounds)
    
    if location(identity, dev):
        factors.append(Factor(rule=0, basis='general', effect='boost', detail='Matches your location'))
        score += 0.04
        
    score = min(1.0, score * recency_factor(dev.occurredAt, now))
    basis = BASIS_BY_RULE[top['rule']]

    return RelevanceDecision(
        show=True,
        suppressed=False,
        suppressedReason=None,
        suppressedMemoryId=None,
        basis=basis,
        personalRelevance=round(score, 3),
        factors=factors,
        ambitionId=ambition_id,
        whyItMatters=top['why'],
        couldChange=could_change,
        attention=decide_attention(DecideAttentionInput(basis=basis, score=score, impact=assumption)),
        assumption=assumption,
        needsAmbitionAssessment=needs_ambition_assessment,
    )
