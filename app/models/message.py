from pydantic import BaseModel
from typing import Optional

class MessageCreate(BaseModel):
    content: str

class MessageResponse(BaseModel):
    id: str
    chat_id: str
    sender_id: str
    content: Optional[str] = None
    sticker_id: Optional[str] = None
    content: str
    created_at: int
    is_read: bool