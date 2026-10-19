from typing import List, Optional, Set, Dict
from pydantic import BaseModel
from datetime import datetime

from src.domain.types import Attention, RelevanceBasis

# Pure grouping of the per-user evaluation ledger into the "lens": what the world is talking about
# versus what matters to this user, and what was set aside and why. No I/O.

LENS_THRESHOLDS = {
    'bigInWorld': 0.6,
    'weakPersonal': 0.5,
    'quietWorld': 0.4,
    'strongPersonal': 0.6,
    'maxPerList': 3,
    'maxHidden': 5,
}

MEMORY_BASES = ['temporary_interest', 'preference', 'interest', 'recent_interest']

class CoverageEntry(BaseModel):
    developmentId: str
    storyId: str
    storyTitle: str
    headline: str
    occurredAt: str
    worldSignificance: Optional[float] = None
    personalRelevance: float
    shown: bool
    suppressed: bool
    suppressedReason: Optional[str] = None
    suppressedMemoryId: Optional[str] = None
    basis: Optional[RelevanceBasis] = None
    attention: Optional[Attention] = None

class FeedbackEntry(BaseModel):
    id: str
    createdAt: str
    feedbackType: str
    storyTitle: Optional[str] = None
    reason: Optional[str] = None

class LensItem(BaseModel):
    developmentId: str
    storyId: str
    headline: str
    storyTitle: str
    worldSignificance: Optional[float] = None
    personalRelevance: float

class HiddenItem(BaseModel):
    developmentId: str
    storyId: str
    headline: str
    storyTitle: str
    reason: str
    memoryId: Optional[str] = None

class LearningEvent(BaseModel):
    id: str
    createdAt: str
    storyTitle: Optional[str] = None
    effect: str
    reason: Optional[str] = None

class MovedUpItem(BaseModel):
    developmentId: str
    headline: str
    basis: RelevanceBasis

class Funnel(BaseModel):
    checked: int
    shown: int
    important: int
    needsAttention: int

class Learning(BaseModel):
    events: List[LearningEvent]
    movedUp: List[MovedUpItem]

class Lens(BaseModel):
    checked: int
    setAside: int
    funnel: Funnel
    bigInWorld: List[LensItem]
    quietButYours: List[LensItem]
    hidden: List[HiddenItem]
    learning: Learning

# Wording mirrors what applyFeedback actually stores, so the strip never claims an effect that did not happen.
FEEDBACK_EFFECTS: Dict[str, str] = {
    'not_relevant': 'Story hidden',
    'already_know': 'This update hidden',
    'too_much': 'Story paused for 7 days',
    'not_interested_temporarily': 'Story paused',
    'less_like_this': 'Similar stories reduced for 30 days',
    'more_like_this': 'Interest added, similar stories rank higher',
    'relevant': 'Interest added, similar stories rank higher',
    'source_not_trusted': 'Source hidden',
    'track': 'Story tracked',
    'untrack': 'Story untracked',
}

def feedback_effect(feedback_type: str) -> Optional[str]:
    return FEEDBACK_EFFECTS.get(feedback_type)

def latest_per_story(entries: List[CoverageEntry]) -> List[CoverageEntry]:
    # One entry per story: the most recent development, so a story with many updates appears once.
    latest: Dict[str, CoverageEntry] = {}
    
    for entry in entries:
        current = latest.get(entry.storyId)
        # Parse occurredAt as naive datetime if it has Z, else rely on fromisoformat
        entry_time = datetime.fromisoformat(entry.occurredAt.replace('Z', '+00:00'))
        
        if not current:
            latest[entry.storyId] = entry
        else:
            current_time = datetime.fromisoformat(current.occurredAt.replace('Z', '+00:00'))
            if entry_time > current_time:
                latest[entry.storyId] = entry
                
    return list(latest.values())

def to_lens_item(e: CoverageEntry) -> LensItem:
    return LensItem(
        developmentId=e.developmentId,
        storyId=e.storyId,
        headline=e.headline,
        storyTitle=e.storyTitle,
        worldSignificance=e.worldSignificance,
        personalRelevance=e.personalRelevance
    )

def build_lens(coverage: List[CoverageEntry], feedback: List[FeedbackEntry]) -> Lens:
    visible = [c for c in coverage if not c.suppressed]

    big_in_world_filtered = [
        c for c in visible
        if (c.worldSignificance or 0) >= LENS_THRESHOLDS['bigInWorld'] and c.personalRelevance < LENS_THRESHOLDS['weakPersonal']
    ]
    big_in_world = latest_per_story(big_in_world_filtered)
    big_in_world.sort(key=lambda x: x.worldSignificance or 0, reverse=True)
    big_in_world = [to_lens_item(x) for x in big_in_world[:LENS_THRESHOLDS['maxPerList']]]

    quiet_but_yours_filtered = [
        c for c in visible
        if c.shown and c.worldSignificance is not None and c.worldSignificance < LENS_THRESHOLDS['quietWorld'] and c.personalRelevance >= LENS_THRESHOLDS['strongPersonal']
    ]
    quiet_but_yours = latest_per_story(quiet_but_yours_filtered)
    quiet_but_yours.sort(key=lambda x: x.personalRelevance, reverse=True)
    quiet_but_yours = [to_lens_item(x) for x in quiet_but_yours[:LENS_THRESHOLDS['maxPerList']]]

    hidden_filtered = [c for c in coverage if c.suppressed and c.suppressedReason]
    hidden_latest = latest_per_story(hidden_filtered)[:LENS_THRESHOLDS['maxHidden']]
    hidden = [
        HiddenItem(
            developmentId=c.developmentId,
            storyId=c.storyId,
            headline=c.headline,
            storyTitle=c.storyTitle,
            reason=c.suppressedReason or '',
            memoryId=c.suppressedMemoryId
        ) for c in hidden_latest
    ]

    seen_events: Set[str] = set()
    events: List[LearningEvent] = []
    
    for f in feedback:
        effect = feedback_effect(f.feedbackType)
        key = f"{f.storyTitle or ''}|{effect or ''}"
        if not effect or key in seen_events:
            continue
        seen_events.add(key)
        events.append(LearningEvent(
            id=f.id,
            createdAt=f.createdAt,
            storyTitle=f.storyTitle,
            effect=effect,
            reason=f.reason
        ))

    moved_up_filtered = [c for c in coverage if c.shown and c.basis is not None and c.basis in MEMORY_BASES]
    moved_up_latest = latest_per_story(moved_up_filtered)[:LENS_THRESHOLDS['maxPerList']]
    moved_up = [
        MovedUpItem(
            developmentId=c.developmentId,
            headline=c.headline,
            basis=c.basis
        ) for c in moved_up_latest if c.basis is not None
    ]

    shown_entries = [c for c in coverage if c.shown]
    
    return Lens(
        checked=len(coverage),
        setAside=len([c for c in coverage if not c.shown]),
        funnel=Funnel(
            checked=len(coverage),
            shown=len(shown_entries),
            important=len([c for c in shown_entries if c.attention in ('act', 'know')]),
            needsAttention=len([c for c in shown_entries if c.attention == 'act'])
        ),
        bigInWorld=big_in_world,
        quietButYours=quiet_but_yours,
        hidden=hidden,
        learning=Learning(events=events[:5], movedUp=moved_up)
    )
