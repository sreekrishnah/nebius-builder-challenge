import json
from datetime import date, datetime, timezone
from typing import List, Dict, Any, Optional

from src.lib.logger import create_logger
from src.lib.supabase import supabase, unwrap, unwrap_maybe
from src.providers.intelligence import (
    StructuredDailyReport,
    SourceReference,
    generate_with_preferred_engine,
    get_active_model_name,
)

log = create_logger('report-intelligence')

REPORT_SYNTHESIS_PROMPT = """You are a senior intelligence analyst producing a personal daily intelligence briefing for this specific user.
Today's date is {today}.
GROUNDING RULES:
1. Use ONLY the real-world developments and evidence sources listed below.
2. NEVER invent facts, sources, quotes, statistics, URLs, dates, or relationships.
3. Every key development and recommendation MUST link to at least one valid source URL provided in the evidence.
4. Distinguish between what is known fact and what is reasoned implication.
5. In your trajectory assessment:
   - status MUST be one of: 'no_material_change', 'incremental_development', 'emerging_opportunity', 'emerging_risk', or 'trajectory_shift'.
   - Do NOT exaggerate routine news into a trajectory shift.
6. Connect current developments to previous historical developments when a genuine relationship exists (trend continuation, reversal, contradiction, or dependency).
7. If evidence is insufficient or uncertain, explicitly state the uncertainty rather than guessing.
8. Always give two to four recommended_actions: concrete next steps for this user, each tied to a goal and to source URLs from the evidence.

USER CONTEXT:
Ambition: {ambition}
Role: {role}
Current activity: {activity}
Goals: {goals_json}
Preferences / Interests: {preferences_json}
Exclusions / Suppressed topics: {suppressions_json}
User Assumptions: {assumptions_json}

RETRIEVED DEVELOPMENTS & EVIDENCE:
{developments_json}

HISTORICAL MEMORY CONTEXT:
{history_json}
"""

async def synthesize_daily_report(
    user_id: str,
    ambition_ctx: Dict[str, Any],
    developments: List[Dict[str, Any]],
    historical_context: List[Dict[str, Any]],
    persisted_sources: List[SourceReference],
    research_plan_meta: Optional[Dict[str, Any]] = None,
    activity_trace: Optional[List[Dict[str, Any]]] = None
) -> StructuredDailyReport:
    """Uses NVIDIA Nemotron via Nebius Token Factory to synthesize a structured, evidence-grounded Daily Report."""
    devs_json = json.dumps([
        {
            "headline": d.get("headline"),
            "summary": d.get("summary"),
            "what_changed": d.get("what_changed"),
            "category": d.get("category"),
            "occurred_at": d.get("occurred_at"),
            "sources": d.get("sources", [])
        }
        for d in developments[:12]
    ], indent=2)

    prompt = REPORT_SYNTHESIS_PROMPT.format(
        today=date.today().isoformat(),
        ambition=ambition_ctx.get("ambition", "Active Ambition"),
        role=ambition_ctx.get("role", "Professional"),
        activity=ambition_ctx.get("activity", "Building"),
        goals_json=json.dumps(ambition_ctx.get("goals", [])),
        preferences_json=json.dumps(ambition_ctx.get("preferences", [])),
        suppressions_json=json.dumps(ambition_ctx.get("suppressions", [])),
        assumptions_json=json.dumps(ambition_ctx.get("assumptions", [])),
        developments_json=devs_json,
        history_json=json.dumps(historical_context[:8], indent=2) if historical_context else "No prior reports available.",
    )

    log.info("synthesizing structured report with Nemotron", {"userId": user_id, "developmentsCount": len(developments)})

    report = await generate_with_preferred_engine(
        task="synthesizeDailyReport",
        prompt=prompt,
        schema_cls=StructuredDailyReport,
        system_instruction="You are an expert intelligence analyst specializing in strategic ambition tracking and verified evidence synthesis.",
        temperature=0.2
    )

    # Reconcile sources: ensure all report sources come from verified retrieved sources
    verified_url_map = {s.url: s for s in persisted_sources}
    final_sources: List[SourceReference] = []

    # Include sources cited in key developments
    for kd in report.key_developments:
        valid_urls = []
        for url in kd.source_urls:
            if url in verified_url_map:
                valid_urls.append(url)
                if verified_url_map[url] not in final_sources:
                    final_sources.append(verified_url_map[url])
            elif persisted_sources:
                # If model hallucinates a URL, point to actual retrieved source
                fallback = persisted_sources[0]
                if fallback.url not in valid_urls:
                    valid_urls.append(fallback.url)
                if fallback not in final_sources:
                    final_sources.append(fallback)
        kd.source_urls = valid_urls if valid_urls else ([persisted_sources[0].url] if persisted_sources else [])

    # Include general sources if not empty
    if not final_sources and persisted_sources:
        final_sources = persisted_sources[:10]

    report.sources = final_sources
    return report

async def save_structured_report(
    report_id: str,
    structured_report: StructuredDailyReport,
    research_plan_meta: Optional[Dict[str, Any]] = None,
    activity_trace: Optional[List[Dict[str, Any]]] = None
) -> None:
    """Persists structured intelligence fields to Supabase daily_reports table."""
    try:
        update_data = {
            "executive_summary": structured_report.executive_summary,
            "trajectory_assessment": structured_report.trajectory_assessment.dict(),
            "historical_connections": [hc.dict() for hc in structured_report.historical_connections],
            "opportunities": [o.dict() for o in structured_report.opportunities],
            "risks": [r.dict() for r in structured_report.risks],
            "recommended_actions": [ra.dict() for ra in structured_report.recommended_actions],
            "sources": [s.dict() for s in structured_report.sources],
            "uncertainties": structured_report.uncertainties,
            "research_plan": research_plan_meta,
            "activity_trace": activity_trace,
            "model_used": get_active_model_name(),
        }
        supabase.table("daily_reports").update(update_data).eq("id", report_id).execute()
        log.info("structured daily report persisted", {"reportId": report_id, "model": update_data["model_used"]})
    except Exception as err:
        log.error("failed to persist structured daily report", {"reportId": report_id, "err": str(err)})
