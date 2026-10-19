import pytest
from src.providers.intelligence import FeedbackInterpretation

def test_feedback_interpretation_schema():
    interpretation = FeedbackInterpretation(
        feedback_type="topic_preference_correction",
        operation="decrease_priority",
        confidence=0.98,
        explicit=True,
        proposed_memory_type="suppression",
        proposed_topic="robotics hardware",
        proposed_content="User explicitly prefers the software layer; deprioritize robotics hardware manufacturing.",
        reason="User stated: I only care about the AI software layer, not robotics hardware."
    )
    assert interpretation.feedback_type == "topic_preference_correction"
    assert interpretation.operation == "decrease_priority"
    assert interpretation.proposed_topic == "robotics hardware"
    assert interpretation.explicit is True
    assert interpretation.proposed_memory_type == "suppression"

def test_career_ambition_adjustment_schema():
    interpretation = FeedbackInterpretation(
        feedback_type="ambition_change",
        operation="update_ambition",
        confidence=0.95,
        explicit=True,
        proposed_memory_type="identity",
        proposed_topic="career ambition",
        proposed_content="Transitioned from hardware engineer to autonomous agent systems architect.",
        reason="User announced shift in career path."
    )
    assert interpretation.proposed_memory_type == "identity"
    assert interpretation.feedback_type == "ambition_change"
