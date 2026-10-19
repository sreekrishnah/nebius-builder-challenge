from typing import Literal, Optional, Dict, Any, TypeGuard
from datetime import datetime
from pydantic import BaseModel

CONTINUITY = ['new', 'updated', 'confirmed', 'contradicted', 'escalated', 'resolved', 'consequence']
Continuity = Literal['new', 'updated', 'confirmed', 'contradicted', 'escalated', 'resolved', 'consequence']

# Mirrors the memory priority ladder: earlier is stronger. 'general' is world importance only.
RELEVANCE_BASES = [
    'temporary_interest',
    'preference',
    'ambition',
    'tracked',
    'interest',
    'recent_interest',
    'general',
]
RelevanceBasis = Literal[
    'temporary_interest',
    'preference',
    'ambition',
    'tracked',
    'interest',
    'recent_interest',
    'general',
]

# act: a stated assumption is challenged. know: explicit interest or a strong ambition link. fyi: everything else shown.
ATTENTION = ['act', 'know', 'fyi']
Attention = Literal['act', 'know', 'fyi']

MEMORY_TYPES = [
    'identity',
    'interest',
    'temporary_interest',
    'tracked_entity',
    'suppression',
    'temporary_suppression',
]
MemoryType = Literal[
    'identity',
    'interest',
    'temporary_interest',
    'tracked_entity',
    'suppression',
    'temporary_suppression',
]

MemorySource = Literal['explicit', 'inferred', 'onboarding']

class MemoryRow(BaseModel):
    id: str
    user_id: str
    memory_type: MemoryType
    topic: Optional[str] = None
    content: str
    strength: Optional[float] = None
    confidence: Optional[float] = None
    source: Optional[MemorySource] = None
    valid_until: Optional[str] = None  # Using str for ISO datetimes to match TS
    status: Literal['active', 'archived']
    structured_value: Optional[Dict[str, Any]] = None
    priority: Optional[int] = None
    created_at: str
    updated_at: str

class AmbitionRow(BaseModel):
    id: str
    user_id: str
    title: str
    description: Optional[str] = None
    horizon: Optional[str] = None
    geography: Optional[str] = None
    status: str
    priority: int
    expires_at: Optional[str] = None
    created_at: str

def is_continuity(value: Any) -> TypeGuard[Continuity]:
    return value in CONTINUITY

def is_attention(value: Any) -> TypeGuard[Attention]:
    return value in ATTENTION
