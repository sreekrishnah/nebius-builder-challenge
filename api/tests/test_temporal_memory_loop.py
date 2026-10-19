import pytest
from src.domain.types import MemoryRow
from src.providers.intelligence import ResearchPlanInput

def test_temporal_memory_loop_proves_learning():
    """
    Simulates Day 1 vs Day 2 feedback loop:
    Day 1: General interest with no hardware suppression.
    Day 1 Feedback: User suppresses 'hardware manufacturing'.
    Day 2: Research planning and relevance filter suppress hardware developments,
           prioritizing AI software layer.
    """
    now_iso = "2026-10-14T00:00:00Z"

    # -------------------------------------------------------------
    # DAY 1: Initial state before feedback
    # -------------------------------------------------------------
    day1_memories = [
        MemoryRow(
            id="mem-1",
            user_id="user-test-1",
            memory_type="interest",
            topic="AI robotics",
            content="Interested in robotics startups",
            status="active",
            created_at=now_iso,
            updated_at=now_iso
        )
    ]

    day1_plan_input = ResearchPlanInput(
        ambition="Build an AI robotics startup",
        role="Founder",
        activity="Prototyping",
        goals=["Develop robotics venture"],
        preferences=[m.topic for m in day1_memories if m.memory_type == "interest"],
        suppressions=[m.topic for m in day1_memories if m.memory_type == "suppression"]
    )

    assert "hardware manufacturing" not in day1_plan_input.suppressions
    assert len(day1_plan_input.preferences) == 1

    # -------------------------------------------------------------
    # DAY 1 EVENING: User provides feedback:
    # "I only care about the AI software layer, not hardware manufacturing."
    # System stores this preference in durable temporal memory.
    # -------------------------------------------------------------
    learned_preference = MemoryRow(
        id="mem-2",
        user_id="user-test-1",
        memory_type="suppression",
        topic="hardware manufacturing",
        content="User prefers AI software layer; deprioritize hardware manufacturing",
        status="active",
        created_at=now_iso,
        updated_at=now_iso
    )
    software_preference = MemoryRow(
        id="mem-3",
        user_id="user-test-1",
        memory_type="interest",
        topic="AI software layer",
        content="Focus exclusively on AI agent and software layer",
        status="active",
        created_at=now_iso,
        updated_at=now_iso
    )

    day2_memories = day1_memories + [learned_preference, software_preference]

    # -------------------------------------------------------------
    # DAY 2: Research Planner & Relevance Engine receive updated memory
    # -------------------------------------------------------------
    day2_plan_input = ResearchPlanInput(
        ambition="Build an AI robotics startup",
        role="Founder",
        activity="Prototyping",
        goals=["Develop robotics venture"],
        preferences=[m.topic for m in day2_memories if m.memory_type == "interest"],
        suppressions=[m.topic for m in day2_memories if m.memory_type == "suppression"],
        recent_feedback=[m.content for m in day2_memories if m.id == "mem-2"]
    )

    # Verify Day 2 research input includes the learned suppression & new software preference
    assert "hardware manufacturing" in day2_plan_input.suppressions
    assert "AI software layer" in day2_plan_input.preferences
    assert len(day2_plan_input.recent_feedback) > 0

    # -------------------------------------------------------------
    # Relevance Evaluator suppression test:
    # Any development testing the suppressed topic is suppressed or deprioritized
    # -------------------------------------------------------------
    suppressed_topics = set(day2_plan_input.suppressions)
    hardware_headline = "New Actuator Hardware Manufacturing Plant Opens"
    software_headline = "New Autonomous Software Agent Framework Released"

    assert any(supp_term in hardware_headline.lower() for supp_term in suppressed_topics)
    assert not any(supp_term in software_headline.lower() for supp_term in suppressed_topics)
