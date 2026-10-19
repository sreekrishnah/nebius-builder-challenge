from typing import Optional
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr, Field

from src.config.env import env
from src.lib.errors import AppError, errors
from src.lib.logger import create_logger
from src.lib.password import hash_password, verify_password
from src.lib.supabase import supabase, unwrap, unwrap_maybe
from src.lib.tokens import sign_token
from src.http.middleware import get_current_user, auth_limiter

log = create_logger('auth')
router = APIRouter()

SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
WS_TTL_SECONDS = 5 * 60

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    fullName: Optional[str] = Field(None, min_length=1, max_length=120)

class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)

def public_user(u: dict):
    return {"id": u['id'], "email": u['email'], "fullName": u.get('full_name')}

def session_for(user_id: str):
    return sign_token(user_id, 'session', SESSION_TTL_SECONDS, env.AUTH_SECRET)

DECOY_HASH = f"{'aa'*16}:{'bb'*64}"

@router.post('/register', status_code=201)
async def register(req_data: RegisterRequest, request: Request):
    auth_limiter.check(request.client.host if request.client else 'unknown')
    email = req_data.email.strip().lower()
    
    existing_res = supabase.table('users').select('id').eq('email', email).maybe_single().execute()
    existing = unwrap_maybe('users.find', existing_res)
    if existing:
        raise AppError('EMAIL_TAKEN', 409, 'An account with this email already exists.')
        
    hashed = hash_password(req_data.password)
    insert_res = supabase.table('users').insert({
        "email": email,
        "full_name": req_data.fullName.strip() if req_data.fullName else None,
        "password_hash": hashed
    }).select('id, email, full_name, password_hash').execute()
    
    user = None
    if insert_res.data and len(insert_res.data) > 0:
        user = insert_res.data[0]
    else:
        # Might be caught by uniqueness constraint but handled softly
        raise AppError('EMAIL_TAKEN', 409, 'An account with this email already exists.')
        
    log.info('user registered', {"userId": user['id']})
    return {"success": True, "token": session_for(user['id']), "user": public_user(user)}

@router.post('/login')
async def login(req_data: LoginRequest, request: Request):
    auth_limiter.check(request.client.host if request.client else 'unknown')
    email = req_data.email.strip().lower()
    
    res = supabase.table('users').select('id, email, full_name, password_hash').eq('email', email).maybe_single().execute()
    user = unwrap_maybe('users.login', res)
    
    valid = verify_password(req_data.password, user['password_hash'] if user and user.get('password_hash') else DECOY_HASH)
    
    if not user or not valid:
        log.warn('login failed', {"emailKnown": bool(user)})
        raise AppError('INVALID_CREDENTIALS', 401, 'Invalid email or password.')
        
    return {"success": True, "token": session_for(user['id']), "user": public_user(user)}

@router.get('/me')
async def me(user_id: str = Depends(get_current_user)):
    res = supabase.table('users').select('id, email, full_name, password_hash').eq('id', user_id).maybe_single().execute()
    user = unwrap_maybe('users.me', res)
    if not user:
        raise errors.unauthenticated()
        
    ambition_res = supabase.table('ambitions').select('id').eq('user_id', user_id).eq('status', 'active').limit(1).maybe_single().execute()
    ambition = unwrap_maybe('ambitions.any', ambition_res)
    
    return {"success": True, "user": public_user(user), "hasAmbition": ambition is not None}

@router.post('/ws-token')
async def ws_token(user_id: str = Depends(get_current_user)):
    token = sign_token(user_id, 'ws', WS_TTL_SECONDS, env.AUTH_SECRET)
    return {"success": True, "token": token, "expiresInSec": WS_TTL_SECONDS}
