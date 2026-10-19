from typing import Optional, Callable
from fastapi import Request, HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
import time

from src.config.env import env
from src.lib.errors import AppError, errors
from src.lib.logger import create_logger
from src.lib.tokens import verify_token

log = create_logger('http')

security = HTTPBearer()

def get_current_user(credentials: HTTPAuthorizationCredentials = Security(security)) -> str:
    token = credentials.credentials
    payload = verify_token(token, 'session', env.AUTH_SECRET)
    if not payload or not payload.get('sub'):
        raise HTTPException(status_code=401, detail="Unauthenticated")
    return payload['sub']

class RateLimiter:
    def __init__(self, max_hits: int, window_ms: int):
        self.max_hits = max_hits
        self.window_ms = window_ms
        self.hits = {}
        
    def check(self, ip: str) -> None:
        now = time.time() * 1000
        entry = self.hits.get(ip)
        
        if not entry or entry['resetAt'] <= now:
            self.hits[ip] = {'count': 1, 'resetAt': now + self.window_ms}
            return
            
        entry['count'] += 1
        if entry['count'] > self.max_hits:
            raise AppError('RATE_LIMITED', 429, 'Too many requests. Please slow down.')

# Shared rate limiters
auth_limiter = RateLimiter(30, 15 * 60 * 1000)

class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        try:
            response = await call_next(request)
            return response
        except AppError as e:
            if e.status >= 500:
                log.error('request failed', {"path": request.url.path, "code": e.code, "diagnostic": getattr(e, 'diagnostic', None)})
            return JSONResponse(
                status_code=e.status,
                content={"success": False, "error": {"code": e.code, "message": getattr(e, 'safeMessage', str(e))}}
            )
        except Exception as e:
            log.error('unhandled error', {"path": request.url.path, "err": str(e)})
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": {"code": 'INTERNAL', "message": 'An unexpected error occurred.'}}
            )
