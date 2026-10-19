import asyncio
import json
import re
import time
from typing import Dict, Any, List, Optional, Type, TypeVar
import httpx
from pydantic import BaseModel, ValidationError

from src.config.env import env
from src.lib.errors import AppError, errors
from src.lib.logger import create_logger

log = create_logger('nebius')

T = TypeVar('T', bound=BaseModel)

MAX_RETRIES = 3
INITIAL_BACKOFF_SECONDS = 1.0
TIMEOUT_SECONDS = 60.0

def nebius_configured() -> bool:
    return bool(env.NEBIUS_API_KEY and env.NEBIUS_API_KEY.strip())

def clean_json_text(text: str) -> str:
    """Extract and clean JSON payload from model response, stripping markdown code fences."""
    cleaned = text.strip()
    # Match ```json ... ``` or ``` ... ```
    fence_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', cleaned, re.IGNORECASE)
    if fence_match:
        cleaned = fence_match.group(1).strip()
    return cleaned

class NebiusClient:
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, model_id: Optional[str] = None):
        self.api_key = api_key or env.NEBIUS_API_KEY
        self.base_url = (base_url or env.NEBIUS_BASE_URL).rstrip('/') + '/'
        self.model_id = model_id or env.NEBIUS_MODEL_ID

    def _headers(self) -> Dict[str, str]:
        if not self.api_key:
            raise AppError("NEBIUS_KEY_MISSING", 401, "NEBIUS_API_KEY is not configured.")
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "AmbitionGazette-Intelligence/1.0",
        }

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int = 4096,
        response_format: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        if not self.api_key:
            raise AppError("NEBIUS_NOT_CONFIGURED", 401, "Nebius API key is not configured. Set NEBIUS_API_KEY in .env.")

        endpoint = f"{self.base_url}chat/completions"
        payload: Dict[str, Any] = {
            "model": self.model_id,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            payload["response_format"] = response_format

        last_error: Optional[Exception] = None

        for attempt in range(1, MAX_RETRIES + 1):
            start_t = time.time()
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                    resp = await client.post(endpoint, headers=self._headers(), json=payload)

                latency = (time.time() - start_t) * 1000

                if resp.status_code == 200:
                    data = resp.json()
                    choices = data.get("choices", [])
                    if not choices:
                        raise errors.model("Nebius response returned no choices.")
                    msg_obj = choices[0].get("message", {})
                    content = msg_obj.get("content")
                    if content is None:
                        content = msg_obj.get("reasoning_content") or msg_obj.get("reasoning") or ""
                    log.info("nebius completion succeeded", {
                        "model": self.model_id,
                        "latencyMs": round(latency, 1),
                        "tokens": data.get("usage", {}).get("total_tokens")
                    })
                    return {
                        "content": content,
                        "raw": data,
                        "latency_ms": latency,
                        "model": self.model_id
                    }

                # Handle specific HTTP statuses
                if resp.status_code in (401, 403):
                    log.error("nebius authentication rejected", {"status": resp.status_code})
                    raise AppError("NEBIUS_AUTH_ERROR", 401, "Nebius Token Factory authentication failed. Verify NEBIUS_API_KEY.")

                if resp.status_code == 404:
                    log.error("nebius model or endpoint not found", {"model": self.model_id, "endpoint": endpoint})
                    raise AppError("NEBIUS_MODEL_NOT_FOUND", 404, f"Configured Nebius model '{self.model_id}' was not found.")

                if resp.status_code == 429 or resp.status_code >= 500:
                    delay = INITIAL_BACKOFF_SECONDS * (2 ** (attempt - 1))
                    log.warn("nebius transient failure, retrying", {
                        "status": resp.status_code,
                        "attempt": attempt,
                        "backoffSeconds": delay
                    })
                    last_error = AppError("NEBIUS_TRANSIENT_ERROR", resp.status_code, f"Nebius error {resp.status_code}")
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(delay)
                        continue

                # Non-retryable error
                resp_text = resp.text[:300]
                log.error("nebius inference error", {"status": resp.status_code, "detail": resp_text})
                raise AppError("NEBIUS_ERROR", resp.status_code, f"Nebius API call failed with status {resp.status_code}.")

            except httpx.TimeoutException:
                latency = (time.time() - start_t) * 1000
                log.warn("nebius request timed out", {"attempt": attempt, "latencyMs": latency})
                last_error = AppError("NEBIUS_TIMEOUT", 504, f"Nebius inference timed out after {TIMEOUT_SECONDS}s.")
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(INITIAL_BACKOFF_SECONDS * attempt)
                    continue
            except (AppError, Exception) as err:
                if isinstance(err, AppError) and err.statusCode in (401, 403, 404):
                    raise
                last_error = err
                if attempt == MAX_RETRIES:
                    break
                await asyncio.sleep(INITIAL_BACKOFF_SECONDS)

        if last_error:
            if isinstance(last_error, AppError):
                raise last_error
            raise errors.model(f"Nebius call failed: {str(last_error)}")
        raise errors.model("Nebius inference failed after retries.")

    async def generate_structured(
        self,
        task: str,
        prompt: str,
        schema_cls: Type[T],
        system_instruction: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> T:
        """Enforces schema compliance on Nebius NVIDIA Nemotron responses with structured output and validation."""
        schema_dict = schema_cls.model_json_schema()
        schema_str = json.dumps(schema_dict, indent=2)

        sys_prompt = system_instruction or "You are an evidence-grounded intelligence agent reasoning over verified facts."
        guidance = (
            f"{sys_prompt}\n\n"
            f"CRITICAL REQUIREMENT: You MUST respond with ONLY a valid JSON object matching this JSON Schema:\n"
            f"{schema_str}\n\n"
            f"Do not include explanation, greetings, preamble, or commentary outside the JSON."
        )

        messages = [
            {"role": "system", "content": guidance},
            {"role": "user", "content": prompt},
        ]

        last_problem = ""
        for attempt in range(1, 3):
            if attempt == 2:
                messages.append({
                    "role": "user",
                    "content": f"Your previous output failed validation: {last_problem}. Return ONLY valid JSON adhering strictly to the schema."
                })

            result = await self.chat_completion(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"}
            )

            raw_text = result["content"]
            cleaned = clean_json_text(raw_text)

            try:
                parsed = schema_cls.model_validate_json(cleaned)
                return parsed
            except (ValidationError, json.JSONDecodeError) as val_err:
                last_problem = str(val_err)
                log.warn("nebius structured parsing failed", {
                    "task": task,
                    "attempt": attempt,
                    "model": self.model_id,
                    "problem": last_problem[:200]
                })

        raise errors.model(f"Nebius model '{self.model_id}' failed to return valid schema for {task}: {last_problem}")

    async def test_inference(self) -> Dict[str, Any]:
        """Perform a real health-check / inference call to Nebius Token Factory."""
        if not self.api_key:
            return {
                "status": "not_configured",
                "ok": False,
                "message": "NEBIUS_API_KEY is not configured",
                "model": self.model_id,
            }
        try:
            res = await self.chat_completion(
                messages=[
                    {"role": "system", "content": "You are a concise intelligence engine."},
                    {"role": "user", "content": "Respond with exactly: 'Nebius Token Factory NVIDIA Nemotron operational'"},
                ],
                max_tokens=256,
                temperature=0.1,
            )
            reply_text = (res.get("content") or "").strip()
            return {
                "status": "healthy",
                "ok": True,
                "model": self.model_id,
                "latencyMs": round(res["latency_ms"], 1),
                "reply": reply_text,
            }
        except Exception as err:
            return {
                "status": "error",
                "ok": False,
                "model": self.model_id,
                "error": str(err),
            }

nebius = NebiusClient()
