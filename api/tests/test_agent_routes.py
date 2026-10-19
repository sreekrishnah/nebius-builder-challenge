import pytest
from datetime import datetime, timezone
from src.routes.agent import DATE_PARAM, to_session

def test_date_param_regex():
    assert DATE_PARAM.match("2026-10-14") is not None
    assert DATE_PARAM.match("2026-10-13") is not None
    assert DATE_PARAM.match("invalid-date") is None
    assert DATE_PARAM.match("2026/10/14") is None

def test_iso_date_parsing_standard_library():
    date_str = "2026-10-14"
    dt = datetime.fromisoformat(f"{date_str}T00:00:00+05:30")
    assert dt.year == 2026
    assert dt.month == 10
    assert dt.day == 14
    assert dt.tzinfo is not None

def test_to_session_resilient():
    assert to_session(None) is None
    assert to_session({}) is None
    assert to_session({"id": "sess-empty"}) is not None
    
    row_with_dict = {
        "id": "sess-1",
        "ended_at": "2026-10-14T12:00:00Z",
        "summary": {
            "summary": "Discussed AI models",
            "keyPoints": ["Point A", "Point B"],
            "sources": [],
            "nextSteps": [{"title": "Step 1", "priority": "high"}]
        }
    }
    s1 = to_session(row_with_dict)
    assert s1["id"] == "sess-1"
    assert s1["summary"] == "Discussed AI models"
    assert len(s1["nextSteps"]) == 1
    assert s1["nextSteps"][0]["priority"] == "High Priority"

    row_with_json_str = {
        "id": "sess-2",
        "ended_at": "2026-10-14T13:00:00Z",
        "summary": '{"summary": "Parsed from json string", "keyPoints": [], "nextSteps": []}'
    }
    s2 = to_session(row_with_json_str)
    assert s2["summary"] == "Parsed from json string"

@pytest.mark.anyio
async def test_get_day_info_endpoint():
    from src.routes.agent import get_day_info
    # Test with valid date and user_id - must not raise 'SyncSelectRequestBuilder' object is not callable
    res = await get_day_info(date="2026-10-14", user_id="00000000-0000-0000-0000-000000000000")
    assert res["success"] is True
    assert res["day"]["date"] == "2026-10-14"
    assert isinstance(res["day"]["sessions"], list)
    assert isinstance(res["day"]["items"], list)

