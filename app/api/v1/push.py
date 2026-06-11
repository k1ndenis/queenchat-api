from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.core.redis import redis_cache
from app.core.dependency import get_current_user
from app.core.database import UserORM as User

router = APIRouter(prefix="/push", tags=["push"])

class PushTokenRequest(BaseModel):
    token: str
    user_id: str
    platform: str = "android"

@router.post("/register")
def register_push_token(
    data: PushTokenRequest,
    current_user: User = Depends(get_current_user)
):
    if data.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="User mismatch")
    
    redis_cache.set(f"push_token:{data.user_id}", data.token)
    return {"status": "ok"}