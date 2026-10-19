from typing import Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from src.services.feedback import FeedbackInput, apply_feedback
from src.services.feedback_interpreter import interpret_and_apply_user_feedback
from src.http.middleware import get_current_user

router = APIRouter()

class TextFeedbackInput(BaseModel):
    feedbackText: str = Field(min_length=2, max_length=1000)
    headline: Optional[str] = None
    reportItemId: Optional[str] = None
    source: Optional[str] = 'web'

@router.post('', include_in_schema=False)
@router.post('/')
async def create_feedback(body: FeedbackInput, user_id: str = Depends(get_current_user)):
    result = await apply_feedback(user_id, body)
    return {"success": True, **result}

@router.post('/interpret')
async def interpret_feedback_route(body: TextFeedbackInput, user_id: str = Depends(get_current_user)):
    """Interprets freeform feedback with NVIDIA Nemotron and applies durable temporal memory updates."""
    result = await interpret_and_apply_user_feedback(
        user_id=user_id,
        feedback_text=body.feedbackText,
        item_headline=body.headline,
        report_item_id=body.reportItemId,
        source=body.source or 'web'
    )
    return {"success": True, **result}
