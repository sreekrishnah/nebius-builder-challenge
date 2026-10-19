from datetime import datetime, timezone
from fastapi import APIRouter

from .agent import router as agent_router
from .ambition import router as ambition_router
from .auth import router as auth_router
from .dashboard import router as dashboard_router
from .feedback import router as feedback_router
from .memory import router as memory_router
from .pipeline import router as pipeline_router
from .plan import router as plan_router
from .story import router as story_router

router = APIRouter()

@router.get('/health')
def health_check():
    return {
        "status": 'ok',
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": 'ambition-gazette-api'
    }

router.include_router(auth_router, prefix="/auth")
router.include_router(dashboard_router, prefix="/dashboard")
router.include_router(ambition_router, prefix="/ambition")
router.include_router(memory_router, prefix="/memory")
router.include_router(story_router, prefix="/story")
router.include_router(feedback_router, prefix="/feedback")
router.include_router(pipeline_router, prefix="/pipeline")
router.include_router(agent_router, prefix="/agent")
router.include_router(plan_router, prefix="/plan")
