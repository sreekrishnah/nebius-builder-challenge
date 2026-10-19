from typing import Dict, Any, List, Optional, Type, TypeVar
from pydantic import BaseModel, Field

from src.config.env import env
from src.lib.errors import AppError, errors
from src.lib.logger import create_logger
from src.providers.nebius import nebius, nebius_configured
from src.lib.gemini import generate_structured as gemini_generate_structured

log = create_logger('intelligence-provider')

T = TypeVar('T', bound=BaseModel)

class ResearchPlanInput(BaseModel):
    ambition: str
    role: str
    activity: str
    geography: Optional[str] = None
    timeline: Optional[str] = None
    goals: List[str] = Field(default_factory=list)
    preferences: List[str] = Field(default_factory=list)
    suppressions: List[str] = Field(default_factory=list)
    recent_feedback: List[str] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)

class ResearchPlan(BaseModel):
    primary_focus: str
    target_queries: List[str] = Field(min_length=2, max_length=6)
    reasoning: str
    risk_query: Optional[str] = None
    opposing_evidence_query: Optional[str] = None

class TrajectoryAssessment(BaseModel):
    status: str = Field(description="no_material_change, incremental_development, emerging_opportunity, emerging_risk, or trajectory_shift")
    outlook: str
    explanation: str
    confidence: float = Field(ge=0.0, le=1.0)

class RecommendedAction(BaseModel):
    action: str
    why_now: str
    supports_goal: str
    evidence_urls: List[str] = Field(default_factory=list)
    time_horizon: str
    uncertainties: Optional[str] = None

class IntelligenceItem(BaseModel):
    title: str
    detail: str
    impact: str
    time_horizon: str
    evidence_urls: List[str] = Field(default_factory=list)

class HistoricalConnection(BaseModel):
    historical_event: str
    current_development: str
    connection_type: str = Field(description="trend_continuation, reversal, contradiction, or related_development")
    explanation: str

class KeyDevelopment(BaseModel):
    headline: str
    what_happened: str
    why_it_matters: str
    what_changed: str
    source_urls: List[str] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    category: str

class SourceReference(BaseModel):
    url: str
    title: str
    publisher: str
    published_date: Optional[str] = None
    query: Optional[str] = None

class StructuredDailyReport(BaseModel):
    executive_summary: str
    trajectory_assessment: TrajectoryAssessment
    key_developments: List[KeyDevelopment] = Field(default_factory=list)
    historical_connections: List[HistoricalConnection] = Field(default_factory=list)
    opportunities: List[IntelligenceItem] = Field(default_factory=list)
    risks: List[IntelligenceItem] = Field(default_factory=list)
    recommended_actions: List[RecommendedAction] = Field(default_factory=list)
    sources: List[SourceReference] = Field(default_factory=list)
    uncertainties: List[str] = Field(default_factory=list)

class FeedbackInterpretation(BaseModel):
    feedback_type: str = Field(description="topic_preference_correction, ambition_change, source_distrust, temporary_pause, or relevance_confirmation")
    operation: str = Field(description="decrease_priority, increase_priority, suppress_topic, pause_topic, or track")
    confidence: float = Field(ge=0.0, le=1.0)
    explicit: bool
    proposed_memory_type: str = Field(description="interest, suppression, temporary_suppression, or identity")
    proposed_topic: str
    proposed_content: str
    reason: str

async def generate_with_preferred_engine(
    task: str,
    prompt: str,
    schema_cls: Type[T],
    system_instruction: Optional[str] = None,
    temperature: float = 0.2,
) -> T:
    """Invokes Nebius Token Factory (NVIDIA Nemotron) as primary intelligence engine, with transparent fallback."""
    if nebius_configured():
        log.info("routing reasoning to Nebius Token Factory (NVIDIA Nemotron)", {"task": task, "model": env.NEBIUS_MODEL_ID})
        return await nebius.generate_structured(
            task=task,
            prompt=prompt,
            schema_cls=schema_cls,
            system_instruction=system_instruction,
            temperature=temperature
        )

    log.warn("Nebius Token Factory not configured; falling back to Gemini for intelligence operation", {"task": task})
    return await gemini_generate_structured(
        task=task,
        prompt=prompt,
        schema_cls=schema_cls,
        temperature=temperature
    )

def get_active_model_name() -> str:
    if nebius_configured():
        return env.NEBIUS_MODEL_ID
    return env.GEMINI_TEXT_MODEL
