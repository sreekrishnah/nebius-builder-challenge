from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel

from src.services.stories import get_timeline, mark_seen, track_story, untrack_story
from src.http.middleware import get_current_user

router = APIRouter()

class StoryRefSchema(BaseModel):
    storyId: str

@router.get('/{id}/timeline')
async def get_story_timeline(id: str = Path(...), peek: str = Query(None), user_id: str = Depends(get_current_user)):
    timeline = await get_timeline(user_id, id)
    if timeline.tracked and peek != '1':
        await mark_seen(user_id, id)
    return {"success": True, "story": timeline.story, "tracked": timeline.tracked, "developments": timeline.developments, "changeSinceLastSeen": timeline.changeSinceLastSeen}

@router.post('/track')
async def track_story_route(body: StoryRefSchema, user_id: str = Depends(get_current_user)):
    await track_story(user_id, body.storyId)
    return {"success": True}

@router.delete('/track')
async def untrack_story_route(body: StoryRefSchema, user_id: str = Depends(get_current_user)):
    await untrack_story(user_id, body.storyId)
    return {"success": True}
