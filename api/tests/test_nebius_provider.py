import asyncio
import pytest
from pydantic import BaseModel, Field
from src.providers.nebius import (
    clean_json_text,
    nebius_configured,
    NebiusClient,
    nebius
)
from src.config.env import env

class SampleSchema(BaseModel):
    name: str
    count: int = Field(ge=0)
    tags: list[str]

def test_clean_json_text_markdown_fences():
    raw_markdown = """```json
{
  "name": "NVIDIA Nemotron",
  "count": 3,
  "tags": ["ai", "reasoning", "nebius"]
}
```"""
    cleaned = clean_json_text(raw_markdown)
    assert cleaned.startswith("{")
    assert cleaned.endswith("}")
    parsed = SampleSchema.model_validate_json(cleaned)
    assert parsed.name == "NVIDIA Nemotron"
    assert parsed.count == 3
    assert len(parsed.tags) == 3

def test_clean_json_text_plain():
    raw_plain = '{"name": "test", "count": 1, "tags": []}'
    cleaned = clean_json_text(raw_plain)
    assert cleaned == raw_plain
    parsed = SampleSchema.model_validate_json(cleaned)
    assert parsed.name == "test"

def test_nebius_client_model_configurable():
    custom_client = NebiusClient(
        api_key="test-key",
        base_url="https://api.studio.nebius.ai/v1/",
        model_id="nvidia/nemotron-3-ultra-550b-a55b"
    )
    assert custom_client.model_id == "nvidia/nemotron-3-ultra-550b-a55b"
    assert custom_client.base_url == "https://api.studio.nebius.ai/v1/"

def test_nebius_live_inference_or_skip():
    """If NEBIUS_API_KEY is configured, run a real health-check / inference call to Nebius Token Factory."""
    if not nebius_configured():
        pytest.skip("NEBIUS_API_KEY is not configured; skipping live provider call.")

    health = asyncio.run(nebius.test_inference())
    assert health["ok"] is True
    assert health["status"] == "healthy"
    assert health["model"] == env.NEBIUS_MODEL_ID
    assert health["latencyMs"] > 0
