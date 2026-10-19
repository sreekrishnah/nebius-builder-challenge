import asyncio
from typing import Literal
from pydantic import BaseModel
from fastapi import APIRouter, Depends, Path

from src.lib.errors import errors
from src.services.ambitions import (
    AmbitionPatchSchema, OnboardingSchema, create_ambition, get_profile,
    list_ambitions, update_ambition, refresh_search_concepts
)
from src.services.assumptions import (
    AssumptionCreateSchema, AssumptionPatchSchema, create_assumption,
    delete_assumption, list_assumptions, update_assumption,
    decide_assumption, suggest_assumptions
)
from src.services.briefing import build_briefing, refresh_briefing, RefreshOptions
from src.services.evolution import get_evolution
from src.http.middleware import get_current_user

router = APIRouter()

class AssumptionDecisionSchema(BaseModel):
    decision: Literal['keep', 'dismiss', 'change_plan']

def to_ambition(a):
    return {
        "id": a.id,
        "title": a.title,
        "description": a.description,
        "horizon": a.horizon,
        "geography": a.geography,
        "priority": a.priority,
        "status": a.status,
        "expiresAt": getattr(a, 'expires_at', None),
        "createdAt": getattr(a, 'created_at', None),
    }

@router.get('', include_in_schema=False)
@router.get('/')
async def list_ambs(user_id: str = Depends(get_current_user)):
    ambitions_task = list_ambitions(user_id)
    profile_task = get_profile(user_id)
    ambitions, profile = await asyncio.gather(ambitions_task, profile_task)
    
    primary = next((a for a in ambitions if a.status == 'active'), None)
    
    resp = {
        "success": True,
        "ambitions": [to_ambition(a) for a in ambitions],
        "profile": {
            "role": profile.role,
            "activity": profile.activity,
            "depth": profile.depth,
            "geographyFocus": profile.geography_focus,
            "topics": profile.categories if profile.categories else [],
            "bidiLanguage": profile.prefered_language,
            "reportLanguage": profile.report_language
        } if profile else None,
    }
    
    if primary:
        resp.update({
            "ambitionId": primary.id,
            "title": primary.title,
            "description": primary.description,
            "horizon": primary.horizon,
            "geography": primary.geography
        })
        
    return resp

@router.post('', include_in_schema=False, status_code=201)
@router.post('/', status_code=201)
async def create_amb(body: OnboardingSchema, user_id: str = Depends(get_current_user)):
    ambition = await create_ambition(user_id, body)
    return {"success": True, "ambitionId": ambition.id, **to_ambition(ambition)}

@router.patch('/{id}')
async def update_amb(body: AmbitionPatchSchema, id: str = Path(...), user_id: str = Depends(get_current_user)):
    ambition = await update_ambition(user_id, id, body)
    await build_briefing(user_id)
    return {"success": True, "ambition": to_ambition(ambition)}

def to_assumption(a):
    return {
        "id": a.id,
        "statement": a.statement,
        "status": a.status,
        "challengedAt": getattr(a, 'challenged_at', None),
        "challengeReason": getattr(a, 'challenge_reason', None),
        "createdAt": getattr(a, 'created_at', None),
    }

@router.get('/{id}/assumptions')
async def list_assumpts(id: str = Path(...), user_id: str = Depends(get_current_user)):
    assumptions = await list_assumptions(user_id, id)
    return {"success": True, "assumptions": [to_assumption(a) for a in assumptions]}

@router.post('/{id}/assumptions', status_code=201)
async def create_assumpt(body: AssumptionCreateSchema, id: str = Path(...), user_id: str = Depends(get_current_user)):
    created = await create_assumption(user_id, id, body.statement)
    await refresh_search_concepts(user_id, id)
    await refresh_briefing(user_id, RefreshOptions(background=True))
    return {"success": True, "assumption": to_assumption(created)}

@router.patch('/{id}/assumptions/{assumption_id}')
async def update_assumpt(body: AssumptionPatchSchema, id: str = Path(...), assumption_id: str = Path(...), user_id: str = Depends(get_current_user)):
    updated = await update_assumption(user_id, id, assumption_id, body)
    if body.statement is not None:
        await refresh_search_concepts(user_id, id)
    await refresh_briefing(user_id, RefreshOptions(background=True))
    return {"success": True, "assumption": to_assumption(updated)}

@router.delete('/{id}/assumptions/{assumption_id}')
async def delete_assumpt(id: str = Path(...), assumption_id: str = Path(...), user_id: str = Depends(get_current_user)):
    await delete_assumption(user_id, id, assumption_id)
    await refresh_search_concepts(user_id, id)
    await refresh_briefing(user_id, RefreshOptions(background=True))
    return {"success": True}

@router.post('/{id}/assumptions/{assumption_id}/decision')
async def decide_assumpt(body: AssumptionDecisionSchema, id: str = Path(...), assumption_id: str = Path(...), user_id: str = Depends(get_current_user)):
    updated = await decide_assumption(user_id, id, assumption_id, body.decision)
    await refresh_briefing(user_id, RefreshOptions(background=True))
    return {"success": True, "assumption": to_assumption(updated)}

@router.post('/{id}/assumptions/suggest')
async def suggest_assumpts(id: str = Path(...), user_id: str = Depends(get_current_user)):
    suggestions = await suggest_assumptions(user_id, id)
    return {"success": True, "suggestions": suggestions}

@router.get('/{id}/evolution')
async def get_evol(id: str = Path(...), user_id: str = Depends(get_current_user)):
    evolution = await get_evolution(user_id, id)
    return {"success": True, "evolution": evolution.dict()}
