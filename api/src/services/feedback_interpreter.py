from typing import Dict, Any, Optional
from datetime import datetime, timezone

from src.lib.logger import create_logger
from src.lib.supabase import supabase, unwrap, unwrap_void
from src.services.memory import remember, MemoryInput, days_from_now
from src.providers.intelligence import (
    FeedbackInterpretation,
    generate_with_preferred_engine
)

log = create_logger('feedback-interpreter')

FEEDBACK_INTERPRETATION_PROMPT = """You are an intelligence agent feedback interpreter.
A user has provided feedback on their daily intelligence report or a specific development.
Extract their intent into structured memory changes so the system learns for the next intelligence run.

Rules:
1. Identify whether this is:
   - 'topic_preference_correction' (e.g. "I only care about the AI software layer, not robotics hardware")
   - 'ambition_change' (e.g. "I switched from robotics founder to AI agent developer")
   - 'source_distrust' (e.g. "I don't trust TechCrunch for deep hardware specs")
   - 'temporary_pause' (e.g. "Too much news about OpenAI this week")
   - 'relevance_confirmation' (e.g. "This was very useful, track this company")
2. Determine the exact operation: 'decrease_priority', 'increase_priority', 'suppress_topic', 'pause_topic', or 'track'.
3. Set proposed_memory_type:
   - 'suppression' for topics to avoid/deprioritize permanently
   - 'temporary_suppression' for topics to pause temporarily (e.g., 7-30 days)
   - 'interest' for topics to prioritize
   - 'identity' for career/role/ambition adjustments
4. proposed_topic: Clean, concise noun phrase (e.g. 'robotics hardware', 'AI software layer', 'TechCrunch').
5. proposed_content: Clear declarative statement of the learned rule.

USER FEEDBACK:
"{feedback_text}"

CONTEXT (if available):
Report item / Headline: {item_headline}
"""

async def interpret_and_apply_user_feedback(
    user_id: str,
    feedback_text: str,
    item_headline: Optional[str] = None,
    report_item_id: Optional[str] = None,
    source: str = "web"
) -> Dict[str, Any]:
    """Uses NVIDIA Nemotron to interpret unstructured user feedback into structured temporal memory updates."""
    prompt = FEEDBACK_INTERPRETATION_PROMPT.format(
        feedback_text=feedback_text.strip(),
        item_headline=item_headline or "General report feedback"
    )

    log.info("interpreting user feedback with Nemotron", {"userId": user_id, "feedback": feedback_text[:60]})

    interpretation = await generate_with_preferred_engine(
        task="interpretFeedback",
        prompt=prompt,
        schema_cls=FeedbackInterpretation,
        system_instruction="You interpret user corrections into durable preference and memory adjustments.",
        temperature=0.1
    )

    valid_until = None
    if interpretation.proposed_memory_type == "temporary_suppression":
        valid_until = days_from_now(14)

    # Persist durable memory update
    memory_row = await remember(
        MemoryInput(
            userId=user_id,
            type=interpretation.proposed_memory_type,
            topic=interpretation.proposed_topic,
            content=interpretation.proposed_content,
            strength=0.9 if interpretation.operation == "increase_priority" else 0.1,
            confidence=interpretation.confidence,
            source="explicit",
            validUntil=valid_until,
            structured={
                "operation": interpretation.operation,
                "feedbackType": interpretation.feedback_type,
                "reason": interpretation.reason,
                "rawText": feedback_text,
                "learnedAt": datetime.now(timezone.utc).isoformat()
            }
        )
    )

    # Save to feedback log with audit delta
    feedback_insert = {
        "user_id": user_id,
        "report_item_id": report_item_id,
        "feedback_type": "relevant" if interpretation.operation == "increase_priority" else "not_relevant",
        "reason": feedback_text,
        "proposed_changes": interpretation.dict(),
        "applied_changes": {
            "memoryId": memory_row.id,
            "topic": memory_row.topic,
            "type": memory_row.memory_type,
            "content": memory_row.content
        },
        "feedback_source": source
    }

    try:
        supabase.table("feedback").insert(feedback_insert).execute()
    except Exception as err:
        log.warn("feedback audit row insert failed", {"err": str(err)})

    log.info("feedback applied to temporal memory", {
        "userId": user_id,
        "topic": interpretation.proposed_topic,
        "operation": interpretation.operation,
        "memoryType": interpretation.proposed_memory_type
    })

    return {
        "success": True,
        "interpretation": interpretation.dict(),
        "memoryId": memory_row.id,
        "message": f"Updated your preferences: {interpretation.proposed_content}"
    }
