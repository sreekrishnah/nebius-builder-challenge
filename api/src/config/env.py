import os
from pathlib import Path
from pydantic import Field, AnyUrl, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional, Literal

_api_dir_env = Path(__file__).resolve().parent.parent.parent / '.env'

class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=('.env', str(_api_dir_env)),
        env_file_encoding='utf-8',
        extra='ignore'
    )

    NODE_ENV: Literal['development', 'test', 'production'] = Field(default='development')
    PORT: int = Field(default=5000, gt=0)
    FRONTEND_URL: str = Field(default='http://localhost:3000')
    GEMINI_API_KEY: str = Field(min_length=1)
    GEMINI_TEXT_MODEL: str = Field(default='gemini-3.5-flash-lite', min_length=1)
    GEMINI_LIVE_MODEL: str = Field(default='gemini-3.8-live', min_length=1)
    GEMINI_EMBEDDING_MODEL: str = Field(default='gemini-embedding-001', min_length=1)
    GNEWS_API_KEY: Optional[str] = Field(default=None)
    SUPABASE_URL: str
    SUPABASE_SERVICE_ROLE_KEY: str = Field(min_length=1)
    DATABASE_URL: Optional[str] = Field(default=None)
    AUTH_SECRET: str = Field(min_length=32)
    CRON_SECRET: Optional[str] = Field(default=None)
    
    # Nebius Token Factory (NVIDIA Nemotron)
    NEBIUS_API_KEY: Optional[str] = Field(default=None)
    NEBIUS_BASE_URL: str = Field(default='https://api.tokenfactory.nebius.com/v1/')
    NEBIUS_MODEL_ID: str = Field(default='nvidia/nemotron-3-super-120b-a12b')
    
    # Tavily Search API
    TAVILY_API_KEY: Optional[str] = Field(default=None)
    TAVILY_SEARCH_DEPTH: Literal['basic', 'advanced'] = Field(default='basic')
    TAVILY_MAX_RESULTS: int = Field(default=5, ge=1, le=20)
    
    VOICE_SESSIONS_PER_DAY: int = Field(default=2, ge=1)
    VOICE_TOKENS_PER_SESSION: int = Field(default=2_000_000, ge=10_000)
    VOICE_TOKENS_PER_DAY: int = Field(default=4_000_000, ge=10_000)
    VOICE_MAX_TOOL_CALLS: int = Field(default=60, ge=1)

def load_env() -> EnvSettings:
    try:
        return EnvSettings()
    except ValidationError as e:
        names = ", ".join([str(loc) for err in e.errors() for loc in err["loc"]])
        raise RuntimeError(f"Invalid or missing environment variables: {names}. See py-api/.env.example.")

env = load_env()
EMBEDDING_DIMENSIONS = 768
