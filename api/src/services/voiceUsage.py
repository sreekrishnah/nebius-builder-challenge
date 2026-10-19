from typing import Literal
from pydantic import BaseModel
from src.config.env import env
from src.lib.errors import errors
from src.lib.supabase import supabase, unwrap_maybe
from src.services.briefing import ist_date

VoiceGate = Literal['ok', 'sessions', 'tokens']

class VoiceUsage(BaseModel):
    sessionsUsed: int
    sessionsLimit: int
    tokensUsed: int
    tokensLimit: int

async def consume_voice_session(user_id: str) -> VoiceGate:
    res = supabase.rpc('consume_voice_session', {
        "p_user_id": user_id,
        "p_session_limit": env.VOICE_SESSIONS_PER_DAY,
        "p_token_limit": env.VOICE_TOKENS_PER_DAY
    }).execute()
    
    data = res.data
    return data if data in ('sessions', 'tokens') else 'ok'

async def refund_voice_session(user_id: str) -> None:
    supabase.rpc('refund_voice_session', {"p_user_id": user_id}).execute()

async def add_voice_tokens(user_id: str, tokens: int) -> None:
    if tokens <= 0:
        return
    supabase.rpc('add_voice_tokens', {"p_user_id": user_id, "p_tokens": round(tokens)}).execute()

async def get_voice_usage(user_id: str) -> VoiceUsage:
    res = supabase.table('daily_voice_usage').select('sessions, tokens')\
        .eq('user_id', user_id).eq('usage_date', ist_date()).maybe_single().execute()
        
    row = unwrap_maybe('voice.usage', res)
    return VoiceUsage(
        sessionsUsed=row['sessions'] if row and row.get('sessions') else 0,
        sessionsLimit=env.VOICE_SESSIONS_PER_DAY,
        tokensUsed=row['tokens'] if row and row.get('tokens') else 0,
        tokensLimit=env.VOICE_TOKENS_PER_DAY
    )

def limit_message(gate: Literal['sessions', 'tokens']) -> str:
    if gate == 'sessions':
        return f"You have used your {env.VOICE_SESSIONS_PER_DAY} voice sessions for today. They reset at midnight IST."
    return "You have reached today's voice usage limit. It resets at midnight IST."
