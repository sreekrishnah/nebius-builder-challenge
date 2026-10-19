from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, Field

from src.lib.errors import errors
from src.services.briefing import build_briefing
from src.services.memory import days_from_now, delete_memory, list_memory, remember, set_memory_status, MemoryInput
from src.http.middleware import get_current_user

router = APIRouter()

def to_item(m):
    return {
        "id": m.id,
        "kind": m.memory_type,
        "topic": m.topic,
        "content": m.content,
        "strength": m.strength,
        "source": m.source,
        "temporary": m.valid_until is not None,
        "expiresAt": m.valid_until,
        "status": m.status,
    }

class InterestSchema(BaseModel):
    topic: str = Field(min_length=2, max_length=120)
    strength: Optional[float] = Field(None, ge=0.0, le=1.0)
    days: Optional[int] = Field(None, ge=1, le=365)

class StatusSchema(BaseModel):
    status: str = Field(pattern="^(active|archived)$")

@router.get('', include_in_schema=False)
@router.get('/')
async def list_memories(user_id: str = Depends(get_current_user)):
    rows = await list_memory(user_id)
    now = datetime.now(timezone.utc).isoformat()
    
    live = [r for r in rows if not r.valid_until or r.valid_until > now]
    
    def pick(types):
        return [to_item(r) for r in live if r.memory_type in types]
        
    return {
        "success": True,
        "identity": pick(['identity']),
        "interests": pick(['interest', 'temporary_interest']),
        "suppressions": pick(['suppression', 'temporary_suppression']),
        "tracked": pick(['tracked_entity'])
    }

@router.post('/interests', status_code=201)
async def create_interest(body: InterestSchema, user_id: str = Depends(get_current_user)):
    item = await remember(MemoryInput(
        userId=user_id,
        type='temporary_interest' if body.days else 'interest',
        topic=body.topic,
        strength=body.strength if body.strength is not None else 0.8,
        confidence=1.0,
        source='explicit',
        validUntil=days_from_now(body.days) if body.days else None
    ))
    await build_briefing(user_id)
    return {"success": True, "item": to_item(item)}

@router.patch('/{id}')
async def update_memory_status(body: StatusSchema, id: str = Path(...), user_id: str = Depends(get_current_user)):
    item = await set_memory_status(user_id, id, body.status)
    await build_briefing(user_id)
    return {"success": True, "item": to_item(item)}

@router.delete('/{id}')
async def delete_memory_item(id: str = Path(...), user_id: str = Depends(get_current_user)):
    await delete_memory(user_id, id)
    await build_briefing(user_id)
    return {"success": True}
