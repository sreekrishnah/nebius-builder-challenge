import hmac
from fastapi import APIRouter, Depends, Request

from src.config.env import env
from src.lib.errors import AppError
from src.services.batch import run_daily_batch
from src.services.pipeline import get_latest_job, start_user_run
from src.http.middleware import get_current_user

router = APIRouter()

def cron_secret_matches(provided: str) -> bool:
    if not env.CRON_SECRET or not provided:
        return False
    # Using secure compare
    return hmac.compare_digest(provided.encode(), env.CRON_SECRET.encode())

@router.post('/run', status_code=202)
async def run_pipeline_route(user_id: str = Depends(get_current_user)):
    # Rate limit could be applied here
    job_id = await start_user_run(user_id)
    return {"success": True, "jobId": job_id}

@router.get('/status')
async def get_status(user_id: str = Depends(get_current_user)):
    job = await get_latest_job(user_id)
    return {
        "success": True,
        "status": job.status if job else 'IDLE',
        "startedAt": job.startedAt if job else None,
        "completedAt": job.completedAt if job else None,
        "error": job.error if job else None,
        "progress": job.progress if job and job.status == 'PROCESSING' else None
    }

@router.post('/batch', status_code=202)
async def run_batch(request: Request):
    provided = request.headers.get('x-cron-secret')
    if not cron_secret_matches(provided):
        raise AppError('FORBIDDEN', 403, 'Forbidden.')
    
    import asyncio
    asyncio.create_task(run_daily_batch())
    return {"success": True}

@router.get('/providers/health')
async def providers_health(user_id: str = Depends(get_current_user)):
    """Verifies live inference for Nebius Token Factory (NVIDIA Nemotron) and Tavily Search API."""
    import asyncio
    from src.providers.nebius import nebius, nebius_configured
    from src.providers.tavily import test_tavily_connection, tavily_configured

    nebius_health, tavily_health = await asyncio.gather(
        nebius.test_inference(),
        test_tavily_connection()
    )

    return {
        "success": True,
        "providers": {
            "nebius": {
                "configured": nebius_configured(),
                "model": env.NEBIUS_MODEL_ID,
                **nebius_health
            },
            "tavily": {
                "configured": tavily_configured(),
                **tavily_health
            },
            "geminiLive": {
                "configured": bool(env.GEMINI_API_KEY),
                "model": env.GEMINI_LIVE_MODEL
            }
        }
    }

