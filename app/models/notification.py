from pydantic import BaseModel
from typing import Optional

class NotificationResponse(BaseModel):
    id: str
    user_id: str
    chat_id: str
    title: str
    message: str
    type: str
    is_read: bool
    created_at: int

class NotificationCreate(BaseModel):
    title: str
    message: str
    type: str = "info"