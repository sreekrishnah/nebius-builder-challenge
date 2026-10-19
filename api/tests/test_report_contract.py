import pytest
from src.providers.intelligence import (
    StructuredDailyReport,
    TrajectoryAssessment,
    KeyDevelopment,
    HistoricalConnection,
    IntelligenceItem,
    RecommendedAction,
    SourceReference,
    ResearchPlan,
)

def test_research_plan_schema_validation():
    plan = ResearchPlan(
        primary_focus="AI software layer developer tools",
        target_queries=[
            "agentic AI coding frameworks",
            "developer tools venture funding 2026",
            "autonomous software development benchmarks"
        ],
        reasoning="Targeted search covering developer tooling advancement, startup funding, and technology shifts.",
        risk_query="developer tools commoditization risk"
    )
    assert len(plan.target_queries) == 3
    assert plan.primary_focus.startswith("AI")
    assert plan.risk_query is not None

def test_daily_report_schema_contract():
    report = StructuredDailyReport(
        executive_summary="Key AI developer framework launches signal rapid acceleration towards autonomous agents.",
        trajectory_assessment=TrajectoryAssessment(
            status="emerging_opportunity",
            outlook="Positive momentum in open software ecosystem",
            explanation="Recent benchmark releases create opening for focused devtools startups.",
            confidence=0.92
        ),
        key_developments=[
            KeyDevelopment(
                headline="Open Source Agent Framework Reaches V2",
                what_happened="New architecture cuts latency by 60%",
                why_it_matters="Directly enables lightweight autonomous development tools",
                what_changed="Latency dropped below interactive threshold",
                source_urls=["https://github.com/blog/agent-v2"],
                confidence=0.95,
                category="technology"
            )
        ],
        historical_connections=[
            HistoricalConnection(
                historical_event="Initial V1 release 6 months ago",
                current_development="V2 architecture launch",
                connection_type="trend_continuation",
                explanation="Confirms consistent multi-month advancement in open agent infrastructure."
            )
        ],
        opportunities=[
            IntelligenceItem(
                title="Build on low-latency agent primitives",
                detail="Opportunity to launch specialist developer agent before incumbents adapt",
                impact="High",
                time_horizon="3-6 months",
                evidence_urls=["https://github.com/blog/agent-v2"]
            )
        ],
        risks=[
            IntelligenceItem(
                title="Rapid API deprecation cycles",
                detail="Underlying libraries changing interfaces rapidly",
                impact="Medium",
                time_horizon="Immediate",
                evidence_urls=["https://github.com/blog/agent-v2"]
            )
        ],
        recommended_actions=[
            RecommendedAction(
                action="Prototype developer CLI using V2 primitives",
                why_now="Early movers establish community standards",
                supports_goal="Build AI developer tools startup",
                evidence_urls=["https://github.com/blog/agent-v2"],
                time_horizon="Next 2 weeks"
            )
        ],
        sources=[
            SourceReference(
                url="https://github.com/blog/agent-v2",
                title="Agent V2 Architecture",
                publisher="github.com",
                published_date="2026-10-12"
            )
        ],
        uncertainties=["Long-term model pricing dynamics remain subject to competition."]
    )

    assert report.trajectory_assessment.status == "emerging_opportunity"
    assert len(report.key_developments) == 1
    assert len(report.sources) == 1
    assert report.sources[0].url == "https://github.com/blog/agent-v2"
    assert len(report.recommended_actions) == 1
