import json
from datetime import date
from typing import List, Dict, Any, Optional

from src.lib.logger import create_logger
from src.providers.intelligence import (
    ResearchPlan,
    ResearchPlanInput,
    generate_with_preferred_engine
)

log = create_logger('research-planner')

RESEARCH_PLAN_PROMPT = """You are a senior intelligence research director creating a targeted, multi-angle research plan.
Today's date is {today}. Search for what is current now; never put a year other than {year} in a query.
Rules:
1. Generate between 3 and 5 high-impact, specific web search queries.
2. The user has a core ambition and specific subgoals/constraints.
3. Queries MUST be targeted and practical: use search terms that appear in real news headlines and analyses.
4. Cover:
   - Primary domain advancement
   - Adjacent technology/ecosystem shift
   - Market forces / funding / policy
   - Emerging risk or counter-evidence
5. Respect user exclusions and suppressions strictly. If the user dislikes a topic or previously asked to ignore it, do NOT generate queries for it.
6. Keep each search query concise (2 to 5 words). Do not use Boolean operators or quotes.

Ambition: {ambition}
Role: {role}
Current activity: {activity}
Geography focus: {geography}
Time horizon: {timeline}
Active goals: {goals_json}
Active user interests/preferences: {preferences_json}
Excluded / suppressed topics: {suppressions_json}
Recent user feedback: {feedback_json}
Assumptions tested: {assumptions_json}
"""

async def build_research_plan(input_data: ResearchPlanInput) -> ResearchPlan:
    """Uses NVIDIA Nemotron via Nebius Token Factory to produce a personalized research plan."""
    prompt = RESEARCH_PLAN_PROMPT.format(
        today=date.today().isoformat(),
        year=date.today().year,
        ambition=input_data.ambition,
        role=input_data.role,
        activity=input_data.activity,
        geography=input_data.geography or "Global",
        timeline=input_data.timeline or "Not specified",
        goals_json=json.dumps(input_data.goals) if input_data.goals else "[]",
        preferences_json=json.dumps(input_data.preferences) if input_data.preferences else "[]",
        suppressions_json=json.dumps(input_data.suppressions) if input_data.suppressions else "[]",
        feedback_json=json.dumps(input_data.recent_feedback) if input_data.recent_feedback else "[]",
        assumptions_json=json.dumps(input_data.assumptions) if input_data.assumptions else "[]",
    )

    log.info("generating research plan with Nemotron", {"ambition": input_data.ambition[:50]})
    plan = await generate_with_preferred_engine(
        task="buildResearchPlan",
        prompt=prompt,
        schema_cls=ResearchPlan,
        system_instruction="You are an autonomous intelligence researcher formulating targeted web search strategies for personal ambition tracking.",
        temperature=0.2
    )

    # Ensure all target queries are distinct and bounded
    distinct_queries = []
    seen = set()
    for q in plan.target_queries:
        clean_q = q.strip()
        if clean_q and clean_q.lower() not in seen:
            seen.add(clean_q.lower())
            distinct_queries.append(clean_q)

    # Include risk query if not duplicate
    if plan.risk_query:
        rq = plan.risk_query.strip()
        if rq and rq.lower() not in seen:
            seen.add(rq.lower())
            distinct_queries.append(rq)

    plan.target_queries = distinct_queries[:5]
    log.info("research plan built", {"queryCount": len(plan.target_queries), "queries": plan.target_queries})
    return plan
