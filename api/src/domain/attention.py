from typing import Literal, Optional, Dict
from pydantic import BaseModel
from src.domain.types import Attention, RelevanceBasis

# Pure attention rules. No I/O.

ATTENTION_THRESHOLDS = {
    'challengeConfidence': 0.6,
    'knowScore': 0.7,
}

AssumptionEffect = Literal['challenges', 'supports', 'none']

class AssumptionImpact(BaseModel):
    assumptionId: str
    effect: AssumptionEffect
    reason: str

ATTENTION_RANK: Dict[Attention, int] = { 'act': 0, 'know': 1, 'fyi': 2 }

KNOW_BASES = ['temporary_interest', 'preference', 'tracked']

def effective_impact(
    impact: Optional[AssumptionImpact],
    relation: Literal['direct', 'indirect', 'none'],
    confidence: float
) -> Optional[AssumptionImpact]:
    """
    A challenge counts only when the development is directly linked to the ambition and the model was
    confident; a weaker challenge would turn every adjacent headline into an alarm.
    """
    if not impact or impact.effect == 'none':
        return None
    
    if impact.effect == 'challenges' and not (relation == 'direct' and confidence >= ATTENTION_THRESHOLDS['challengeConfidence']):
        return None
        
    return impact

class DecideAttentionInput(BaseModel):
    basis: Optional[RelevanceBasis]
    score: float
    impact: Optional[AssumptionImpact]

def decide_attention(input_data: DecideAttentionInput) -> Attention:
    if input_data.impact and input_data.impact.effect == 'challenges':
        return 'act'
    if input_data.basis and input_data.basis in KNOW_BASES:
        return 'know'
    if input_data.basis == 'ambition' and input_data.score >= ATTENTION_THRESHOLDS['knowScore']:
        return 'know'
    return 'fyi'
