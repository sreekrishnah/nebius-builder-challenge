from fastapi import APIRouter, Depends
from src.http.middleware import get_current_user
from src.services.plan import get_plan_status

router = APIRouter()

@router.get('/status', include_in_schema=False)
@router.get('/status/')
async def plan_status(user_id: str = Depends(get_current_user)):
    """Returns the user plan status with all assumptions, real-world evidence, and decision status."""
    plan = await get_plan_status(user_id)
    return {"success": True, "plan": plan}
