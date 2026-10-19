import asyncio
import pytest
from src.services.plan import get_plan_status

def test_plan_status_schema():
    res = asyncio.run(get_plan_status("00000000-0000-0000-0000-000000000000"))
    assert "ambitions" in res
    assert "totals" in res
    assert "checked" in res
    assert "setAside" in res
    assert res["totals"]["assumptions"] == 0
    assert res["totals"]["stable"] == 0
    assert res["totals"]["watch"] == 0
    assert res["totals"]["reconsider"] == 0

def test_assumption_row_watch_status():
    from src.services.assumptions import AssumptionRow
    row = AssumptionRow(
        id="ass-1",
        ambition_id="amb-1",
        statement="Market demand will remain stable",
        status="watch",
        decided_at="2026-10-14T10:00:00Z"
    )
    assert row.status == "watch"
    assert row.decided_at == "2026-10-14T10:00:00Z"

