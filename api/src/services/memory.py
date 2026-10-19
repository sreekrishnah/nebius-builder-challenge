import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any, Literal
from pydantic import BaseModel

from src.domain.types import MemoryRow, MemorySource, MemoryType
from src.lib.gemini import embed_text
from src.lib.logger import create_logger
from src.lib.supabase import supabase, unwrap, unwrap_maybe, unwrap_void, rpc_rows
from src.lib.errors import errors

log = create_logger('memory')

EXACT_PREFIXES = ('story:', 'development:', 'source:')

def normalize_topic(topic: str) -> str:
    # replace multiple whitespaces with single space
    return re.sub(r'\s+', ' ', topic.strip().lower())

def is_exact_topic(topic: str) -> bool:
    return any(topic.startswith(p) for p in EXACT_PREFIXES)

class RememberInput(BaseModel):
    userId: str
    type: MemoryType
    topic: str
    content: Optional[str] = None
    strength: Optional[float] = None
    confidence: Optional[float] = None
    source: MemorySource
    validUntil: Optional[datetime] = None
    structured: Optional[Dict[str, Any]] = None
    priority: Optional[int] = None
    embedding: Optional[List[float]] = None

MemoryInput = RememberInput

def event_row(user_id: str, memory_id: str, event_type: str, old_value: Optional[str], new_value: Optional[str], source: str) -> Dict[str, Any]:
    return {
        "user_id": user_id,
        "memory_id": memory_id,
        "event_type": event_type,
        "old_value": old_value,
        "new_value": new_value,
        "source": source
    }

async def remember(input_data: RememberInput) -> MemoryRow:
    topic = input_data.topic.strip().lower() if input_data.type == 'identity' else normalize_topic(input_data.topic)
    content = input_data.content.strip() if input_data.content and input_data.content.strip() else topic
    
    # Check if exists
    response = supabase.table('user_memory').select('*')\
        .eq('user_id', input_data.userId)\
        .eq('memory_type', input_data.type)\
        .eq('topic', topic)\
        .eq('status', 'active')\
        .maybe_single().execute()
        
    existing = unwrap_maybe('memory.find', response)

    needs_embedding = not is_exact_topic(topic) and input_data.type != 'identity'
    embedding = None
    if needs_embedding:
        embedding = input_data.embedding if input_data.embedding is not None else await embed_text(topic, 'query')
        
    fields = {
        "content": content,
        "strength": input_data.strength,
        "confidence": input_data.confidence,
        "source": input_data.source,
        "valid_until": input_data.validUntil.isoformat() if input_data.validUntil else None,
        "structured_value": input_data.structured,
        "priority": input_data.priority,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    if embedding:
        fields["embedding"] = embedding

    if existing:
        update_response = supabase.table('user_memory').update(fields)\
            .eq('id', existing['id'])\
            .eq('user_id', input_data.userId)\
            .execute()
        row_dict = unwrap('memory.update', update_response)
        # unwrap might return list if single() wasn't appended to update(), python client returns list for update/insert
        row_dict = row_dict[0] if isinstance(row_dict, list) else row_dict
        
        event = event_row(input_data.userId, row_dict['id'], 'UPDATE', existing.get('content'), content, input_data.source)
        unwrap_void('memory.event', supabase.table('memory_events').insert(event).execute())
        
        log.info('memory updated', {"userId": input_data.userId, "type": input_data.type, "topic": topic})
        return MemoryRow(**row_dict)

    insert_fields = {
        "user_id": input_data.userId,
        "memory_type": input_data.type,
        "topic": topic,
        "status": "active",
        **fields
    }
    insert_response = supabase.table('user_memory').insert(insert_fields).execute()
    row_dict = unwrap('memory.insert', insert_response)
    row_dict = row_dict[0] if isinstance(row_dict, list) else row_dict
    
    event = event_row(input_data.userId, row_dict['id'], 'CREATE', None, content, input_data.source)
    unwrap_void('memory.event', supabase.table('memory_events').insert(event).execute())
    
    log.info('memory created', {"userId": input_data.userId, "type": input_data.type, "topic": topic})
    return MemoryRow(**row_dict)

def is_live(row: MemoryRow, now: Optional[datetime] = None) -> bool:
    if now is None:
        now = datetime.utcnow()
    if row.status != 'active':
        return False
    if row.valid_until:
        # replace Z to handle python fromisoformat
        valid_until_dt = datetime.fromisoformat(row.valid_until.replace('Z', '+00:00'))
        # assure now is timezone aware if valid_until is
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return valid_until_dt > now
    return True

async def list_memory(user_id: str, types: Optional[List[MemoryType]] = None) -> List[MemoryRow]:
    query = supabase.table('user_memory').select('*').eq('user_id', user_id).order('created_at', desc=True)
    if types and len(types) > 0:
        query = query.in_('memory_type', types)
    
    response = query.execute()
    rows = unwrap('memory.list', response)
    return [MemoryRow(**r) for r in rows]

async def get_active_memory(user_id: str) -> List[MemoryRow]:
    rows = await list_memory(user_id)
    now = datetime.utcnow().replace(tzinfo=timezone.utc)
    return [r for r in rows if is_live(r, now)]

class MemoryMatch(MemoryRow):
    similarity: float

async def retrieve_similar_memory(user_id: str, embedding: List[float], limit: int = 10) -> List[MemoryMatch]:
    rows = rpc_rows('match_memory', {"p_user_id": user_id, "p_embedding": embedding, "p_limit": limit})
    return [MemoryMatch(**r) for r in rows]

async def get_identity(user_id: str) -> Dict[str, str]:
    rows = await get_active_memory(user_id)
    identity_rows = [r for r in rows if r.memory_type == 'identity']
    identity = {}
    for r in identity_rows:
        if r.topic:
            identity[r.topic] = r.content
    return identity

async def set_memory_status(user_id: str, mem_id: str, status: Literal['active', 'archived']) -> MemoryRow:
    fields = {"status": status, "updated_at": datetime.utcnow().isoformat() + "Z"}
    response = supabase.table('user_memory').update(fields)\
        .eq('id', mem_id)\
        .eq('user_id', user_id)\
        .execute()
        
    row_list = unwrap_maybe('memory.setStatus', response)
    if not row_list:
        raise errors.not_found('Memory item')
        
    row_dict = row_list[0] if isinstance(row_list, list) else row_list
    
    event_type = 'RESTORE' if status == 'active' else 'ARCHIVE'
    event = event_row(user_id, row_dict['id'], event_type, None, None, 'explicit')
    unwrap_void('memory.event', supabase.table('memory_events').insert(event).execute())
    
    return MemoryRow(**row_dict)

async def delete_memory(user_id: str, mem_id: str) -> None:
    response = supabase.table('user_memory').delete().eq('id', mem_id).eq('user_id', user_id).execute()
    row_list = unwrap_maybe('memory.delete', response)
    if not row_list:
        raise errors.not_found('Memory item')

async def archive_expired_memory() -> int:
    now_iso = datetime.utcnow().isoformat() + "Z"
    fields = {"status": "archived", "updated_at": now_iso}
    
    response = supabase.table('user_memory').update(fields)\
        .eq('status', 'active')\
        .lt('valid_until', now_iso)\
        .execute()
        
    rows = unwrap('memory.expire', response)
    return len(rows) if isinstance(rows, list) else 0

def days_from_now(days: int) -> datetime:
    return datetime.utcnow() + timedelta(days=days)
