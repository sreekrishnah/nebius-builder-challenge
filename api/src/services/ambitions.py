import json
from typing import List, Optional, Dict, Any, Literal
from pydantic import BaseModel, Field, root_validator
from datetime import datetime

from src.ai.tasks import AmbitionProfile, extract_ambition_profile
from src.domain.types import AmbitionRow
from src.lib.errors import errors
from src.lib.gemini import embed_text, embed_texts
from src.lib.logger import create_logger
from src.lib.supabase import supabase, unwrap, unwrap_maybe, unwrap_void
from src.services.assumptions import list_assumptions
from src.services.memory import remember, RememberInput

log = create_logger('ambitions')

class OnboardingSchema(BaseModel):
    role: str = Field(min_length=2, max_length=120)
    activity: str = Field(min_length=2, max_length=300)
    ambition: str = Field(min_length=5, max_length=500)
    direction: str = Field(default='', max_length=500)
    timeline: str = Field(min_length=1, max_length=100)
    categories: List[str] = Field(min_items=1, max_items=12)
    geography: str = Field(default='', max_length=120)
    depth: str = Field(default='', max_length=50)
    bidiLang: str = Field(default='English', max_length=50)
    reportLang: str = Field(default='English', max_length=50)

class AmbitionPatchSchema(BaseModel):
    title: Optional[str] = Field(None, min_length=5, max_length=500)
    description: Optional[str] = Field(None, max_length=1500)
    horizon: Optional[str] = Field(None, max_length=100)
    geography: Optional[str] = Field(None, max_length=120)
    priority: Optional[int] = Field(None, ge=1, le=3)
    status: Optional[Literal['active', 'paused', 'achieved', 'archived']] = None

    @root_validator(pre=True)
    def check_at_least_one(cls, values):
        if not values:
            raise ValueError('At least one field is required')
        return values

AMBITION_COLUMNS = 'id, user_id, title, description, horizon, geography, status, priority, expires_at, created_at'

class ProfileRow(BaseModel):
    role: Optional[str] = None
    activity: Optional[str] = None
    depth: Optional[str] = None
    geography_focus: Optional[str] = None
    categories: Optional[List[str]] = None
    prefered_language: Optional[str] = None
    report_language: Optional[str] = None

def embedding_text(title: str, direction: Optional[str] = None, role: Optional[str] = None, activity: Optional[str] = None, geography: Optional[str] = None) -> str:
    parts = [
        title,
        direction,
        f"Role: {role}." if role else "",
        f"Current activity: {activity}." if activity else "",
        f"Location: {geography}." if geography else "",
    ]
    return " ".join([p for p in parts if p]).strip()

async def save_ambition_context(ambition_id: str, profile: AmbitionProfile) -> None:
    fields = {
        "sector": profile.sector,
        "geography": profile.geography,
        "target_market": profile.target_market,
        "time_horizon": profile.time_horizon,
        "goals_json": profile.goals,
        "search_concepts": profile.search_concepts,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    
    response = supabase.table('ambition_context').select('id').eq('ambition_id', ambition_id).limit(1).maybe_single().execute()
    existing = unwrap_maybe('ambition_context.find', response)
    
    if existing:
        update_res = supabase.table('ambition_context').update(fields).eq('id', existing['id']).execute()
        unwrap_void('ambition_context.update', update_res)
    else:
        insert_res = supabase.table('ambition_context').insert({"ambition_id": ambition_id, **fields}).execute()
        unwrap_void('ambition_context.insert', insert_res)

async def list_ambitions(user_id: str) -> List[AmbitionRow]:
    response = supabase.table('ambitions').select(AMBITION_COLUMNS)\
        .eq('user_id', user_id)\
        .neq('status', 'archived')\
        .order('created_at', desc=True).execute()
        
    rows = unwrap('ambitions.list', response)
    rows = rows if isinstance(rows, list) else [rows]
    return [AmbitionRow(**r) for r in rows]

async def get_profile(user_id: str) -> Optional[ProfileRow]:
    response = supabase.table('user_intelligence_profiles')\
        .select('role, activity, depth, geography_focus, categories, prefered_language, report_language')\
        .eq('user_id', user_id).maybe_single().execute()
        
    row = unwrap_maybe('profile.get', response)
    return ProfileRow(**row) if row else None

import asyncio

async def create_ambition(user_id: str, input_data: OnboardingSchema) -> AmbitionRow:
    direction_str = f" Direction: {input_data.direction}." if input_data.direction else ""
    description = f"As {input_data.role}, currently {input_data.activity}. Ambition: {input_data.ambition}.{direction_str}"

    emb_text = embedding_text(title=input_data.ambition, direction=input_data.direction, role=input_data.role, activity=input_data.activity, geography=input_data.geography)
    
    vectors_task = embed_texts([emb_text] + input_data.categories, 'query')
    profile_task = extract_ambition_profile(
        ambition=input_data.ambition,
        role=input_data.role,
        activity=input_data.activity,
        geography=input_data.geography
    )
    
    vectors, profile = await asyncio.gather(vectors_task, profile_task)
    ambition_vector = vectors[0]
    category_vectors = vectors[1:]

    insert_res = supabase.table('ambitions').insert({
        "user_id": user_id,
        "title": input_data.ambition,
        "description": description,
        "horizon": input_data.timeline,
        "geography": input_data.geography or None,
        "status": 'active',
        "priority": 2,
        "embedding": ambition_vector,
    }).select(AMBITION_COLUMNS).execute()
    
    row_dict = unwrap('ambitions.insert', insert_res)
    row_dict = row_dict[0] if isinstance(row_dict, list) else row_dict
    ambition = AmbitionRow(**row_dict)

    try:
        await save_ambition_context(ambition.id, profile)
        
        upsert_res = supabase.table('user_intelligence_profiles').upsert({
            "user_id": user_id,
            "role": input_data.role,
            "activity": input_data.activity,
            "ambition": input_data.ambition,
            "direction": input_data.direction or None,
            "timeline": input_data.timeline,
            "categories": input_data.categories,
            "geography_focus": input_data.geography or None,
            "depth": input_data.depth or None,
            "prefered_language": input_data.bidiLang,
            "report_language": input_data.reportLang,
            "embedding": ambition_vector,
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }).execute()
        unwrap_void('profile.upsert', upsert_res)

        identity = [
            ("role", input_data.role),
            ("profession", input_data.activity),
            ("industry", profile.sector),
            ("location", input_data.geography),
            ("language", input_data.bidiLang),
        ]
        
        for topic, value in identity:
            if value:
                await remember(RememberInput(userId=user_id, type='identity', topic=topic, content=value, source='explicit', confidence=1.0))
                
        for i, category in enumerate(input_data.categories):
            await remember(RememberInput(
                userId=user_id,
                type='interest',
                topic=category,
                strength=0.6,
                confidence=1.0,
                source='onboarding',
                embedding=category_vectors[i]
            ))
            
    except Exception as err:
        supabase.table('ambitions').delete().eq('id', ambition.id).execute()
        raise err

    log.info('ambition created', {"userId": user_id, "ambitionId": ambition.id, "concepts": len(profile.search_concepts)})
    return ambition

async def update_ambition(user_id: str, amb_id: str, patch: AmbitionPatchSchema) -> AmbitionRow:
    response = supabase.table('ambitions').select(AMBITION_COLUMNS).eq('id', amb_id).eq('user_id', user_id).maybe_single().execute()
    current = unwrap_maybe('ambitions.get', response)
    if not current:
        raise errors.not_found('Ambition')

    update_data = {k: v for k, v in patch.dict(exclude_unset=True).items()}
    update_data["updated_at"] = datetime.utcnow().isoformat() + "Z"
    
    text_changed = patch.title is not None or patch.description is not None or patch.geography is not None
    if text_changed:
        title = patch.title if patch.title is not None else current['title']
        geography = patch.geography if patch.geography is not None else current['geography']
        description = patch.description if patch.description is not None else current.get('description') or ''
        
        profile = await get_profile(user_id)
        role = profile.role if profile else None
        activity = profile.activity if profile else None
        
        emb_text = embedding_text(title=title, direction=description, role=role, activity=activity, geography=geography)
        update_data["embedding"] = await embed_text(emb_text, 'query')
        
        assumptions = await list_assumptions(user_id, amb_id)
        refreshed = await extract_ambition_profile(
            ambition=title,
            role=role or '',
            activity=activity or '',
            geography=geography or '',
            assumptions=[a.statement for a in assumptions]
        )
        await save_ambition_context(amb_id, refreshed)

    upd_res = supabase.table('ambitions').update(update_data).eq('id', amb_id).eq('user_id', user_id).select(AMBITION_COLUMNS).execute()
    row_dict = unwrap('ambitions.update', upd_res)
    row_dict = row_dict[0] if isinstance(row_dict, list) else row_dict
    return AmbitionRow(**row_dict)

async def refresh_ambition_index(ambition: AmbitionRow) -> None:
    profile = await get_profile(ambition.user_id)
    role = profile.role if profile else None
    activity = profile.activity if profile else None
    
    emb_text = embedding_text(title=ambition.title, direction=ambition.description or '', role=role, activity=activity, geography=ambition.geography)
    vector = await embed_text(emb_text, 'query')
    
    assumptions = await list_assumptions(ambition.user_id, ambition.id)
    extracted = await extract_ambition_profile(
        ambition=ambition.title,
        role=role or '',
        activity=activity or '',
        geography=ambition.geography or '',
        assumptions=[a.statement for a in assumptions]
    )
    
    upd_amb = supabase.table('ambitions').update({
        "embedding": vector,
        "updated_at": datetime.utcnow().isoformat() + "Z"
    }).eq('id', ambition.id).execute()
    unwrap_void('ambitions.reindex', upd_amb)
    
    await save_ambition_context(ambition.id, extracted)
    
    if profile:
        upd_prof = supabase.table('user_intelligence_profiles').update({"embedding": vector}).eq('user_id', ambition.user_id).execute()
        unwrap_void('profile.reindex', upd_prof)

async def refresh_search_concepts(user_id: str, ambition_id: str) -> None:
    try:
        response = supabase.table('ambitions').select(AMBITION_COLUMNS).eq('id', ambition_id).eq('user_id', user_id).maybe_single().execute()
        ambition = unwrap_maybe('ambitions.concepts', response)
        if not ambition:
            return
            
        profile = await get_profile(user_id)
        role = profile.role if profile else None
        activity = profile.activity if profile else None
        
        assumptions = await list_assumptions(user_id, ambition_id)
        extracted = await extract_ambition_profile(
            ambition=ambition['title'],
            role=role or '',
            activity=activity or '',
            geography=ambition.get('geography') or '',
            assumptions=[a.statement for a in assumptions]
        )
        await save_ambition_context(ambition_id, extracted)
    except Exception as err:
        log.error('search concept refresh failed', {"userId": user_id, "ambitionId": ambition_id, "err": str(err)})
