import hashlib
from typing import List, Optional, Dict, Any, Literal
from pydantic import BaseModel, Field, root_validator
from datetime import datetime, timezone

from src.lib.errors import errors
from src.lib.supabase import supabase, unwrap, unwrap_maybe, unwrap_void

MAX_ASSUMPTIONS_PER_AMBITION = 8

class AssumptionCreateSchema(BaseModel):
    statement: str = Field(min_length=5, max_length=300)

class AssumptionPatchSchema(BaseModel):
    statement: Optional[str] = Field(None, min_length=5, max_length=300)
    status: Optional[str] = None
    
    @root_validator(pre=True)
    def check_at_least_one(cls, values):
        if not values:
            raise ValueError('At least one field is required')
        return values

class AssumptionRow(BaseModel):
    id: str
    ambition_id: str
    statement: str
    status: str = 'holding'
    challenged_by_development_id: Optional[str] = None
    challenged_at: Optional[str] = None
    challenge_reason: Optional[str] = None
    decided_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

COLUMNS = 'id, ambition_id, statement, status, challenged_by_development_id, challenged_at, challenge_reason, decided_at, created_at, updated_at'

async def list_assumptions(user_id: str, ambition_id: Optional[str] = None) -> List[AssumptionRow]:
    query = supabase.table('ambition_assumptions').select(COLUMNS).eq('user_id', user_id).order('created_at', desc=False)
    if ambition_id:
        query = query.eq('ambition_id', ambition_id)
    
    response = query.execute()
    rows = unwrap('assumptions.list', response)
    rows = rows if isinstance(rows, list) else [rows]
    return [AssumptionRow(**r) for r in rows]

async def assert_owns_ambition(user_id: str, ambition_id: str) -> None:
    response = supabase.table('ambitions').select('id').eq('id', ambition_id).eq('user_id', user_id).maybe_single().execute()
    row = unwrap_maybe('assumptions.ambition', response)
    if not row:
        raise errors.not_found('Ambition')

async def create_assumption(user_id: str, ambition_id: str, statement: str) -> AssumptionRow:
    await assert_owns_ambition(user_id, ambition_id)
    existing = await list_assumptions(user_id, ambition_id)
    if len(existing) >= MAX_ASSUMPTIONS_PER_AMBITION:
        raise errors.validation(f"At most {MAX_ASSUMPTIONS_PER_AMBITION} assumptions per ambition.")
        
    statement = statement.strip()
    response = supabase.table('ambition_assumptions').insert({
        "ambition_id": ambition_id,
        "user_id": user_id,
        "statement": statement
    }).select(COLUMNS).execute()
    
    row_dict = unwrap('assumptions.insert', response)
    row_dict = row_dict[0] if isinstance(row_dict, list) else row_dict
    return AssumptionRow(**row_dict)

async def update_assumption(user_id: str, ambition_id: str, assumption_id: str, patch: AssumptionPatchSchema) -> AssumptionRow:
    update_data: Dict[str, Any] = {
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "status": "holding",
        "challenged_by_development_id": None,
        "challenged_at": None,
        "challenge_reason": None
    }
    if patch.statement is not None:
        update_data["statement"] = patch.statement.strip()
        
    response = supabase.table('ambition_assumptions').update(update_data)\
        .eq('id', assumption_id)\
        .eq('ambition_id', ambition_id)\
        .eq('user_id', user_id)\
        .select(COLUMNS).maybe_single().execute()
        
    row_dict = unwrap_maybe('assumptions.update', response)
    if not row_dict:
        raise errors.not_found('Assumption')
    return AssumptionRow(**row_dict)

async def delete_assumption(user_id: str, ambition_id: str, assumption_id: str) -> None:
    response = supabase.table('ambition_assumptions').delete()\
        .eq('id', assumption_id)\
        .eq('ambition_id', ambition_id)\
        .eq('user_id', user_id).execute()
    unwrap_void('assumptions.delete', response)

def assumptions_key(assumptions: List[Dict[str, str]]) -> str:
    """Changes whenever the set or wording of assumptions changes, so cached assessments are redone."""
    # sort by id:statement strings
    text = '|'.join(sorted([f"{a['id']}:{a['statement']}" for a in assumptions]))
    return hashlib.sha1(text.encode('utf-8')).hexdigest()[:12]

async def mark_challenged(assumption_id: str, development_id: str, reason: str) -> None:
    response = supabase.table('ambition_assumptions').update({
        "status": "challenged",
        "challenged_by_development_id": development_id,
        "challenged_at": datetime.now(timezone.utc).isoformat(),
        "challenge_reason": reason
    }).eq('id', assumption_id).eq('status', 'holding').execute()
    unwrap_void('assumptions.challenge', response)

async def decide_assumption(user_id: str, ambition_id: str, assumption_id: str, decision: str) -> AssumptionRow:
    await assert_owns_ambition(user_id, ambition_id)
    if decision == 'dismiss':
        existing = await list_assumptions(user_id, ambition_id)
        found = next((a for a in existing if a.id == assumption_id), None)
        await delete_assumption(user_id, ambition_id, assumption_id)
        if not found:
            raise errors.not_found('Assumption')
        return found
        
    update_data = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "status": "holding",
        "challenged_by_development_id": None,
        "challenged_at": None,
        "challenge_reason": None
    }
    response = supabase.table('ambition_assumptions').update(update_data)\
        .eq('id', assumption_id)\
        .eq('ambition_id', ambition_id)\
        .eq('user_id', user_id)\
        .select(COLUMNS).maybe_single().execute()
        
    row_dict = unwrap_maybe('assumptions.decide', response)
    if not row_dict:
        raise errors.not_found('Assumption')
    return AssumptionRow(**row_dict)

async def suggest_assumptions(user_id: str, ambition_id: str) -> List[Dict[str, str]]:
    await assert_owns_ambition(user_id, ambition_id)
    amb_res = supabase.table('ambitions').select('title, description, horizon, geography').eq('id', ambition_id).maybe_single().execute()
    amb = unwrap_maybe('ambition.suggest', amb_res)
    if not amb:
        raise errors.not_found('Ambition')

    import json
    prompt = (
        f"Ambition: {amb.get('title')}\n"
        f"Description: {amb.get('description')}\n"
        f"Horizon: {amb.get('horizon')}\n"
        f"Geography: {amb.get('geography')}\n\n"
        f"Suggest 3 core assumptions that this ambition critically depends on to succeed. "
        f"For each assumption, provide an 'area' (e.g. Market, Technology, Regulation, Capital) and a concise, testable 'statement' (max 20 words)."
    )
    
    from src.providers.nebius import nebius, nebius_configured
    if nebius_configured():
        try:
            res = await nebius.chat_completion(
                messages=[
                    {"role": "system", "content": "You are a strategic intelligence planner. Return a JSON object with a 'suggestions' list of objects having 'area' and 'statement'."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                max_tokens=512,
                temperature=0.3
            )
            data = json.loads(res["content"])
            if "suggestions" in data and isinstance(data["suggestions"], list):
                return data["suggestions"]
        except Exception:
            pass

    return [
        {"area": "Market Demand", "statement": f"Customer demand for {amb.get('title')} will grow over the next {amb.get('horizon', '2 years')}."},
        {"area": "Execution & Resources", "statement": f"Required capital and technical capabilities remain accessible in {amb.get('geography', 'target markets')}."},
        {"area": "Competitive Landscape", "statement": "Incumbents will not monopolize the primary distribution channels before entry."}
    ]
