from pydantic import BaseModel
from typing import Optional

class MessageCreate(BaseModel):
    content: str
    is_image: bool = False

class MessageResponse(BaseModel):
    id: str
    chat_id: str
    sender_id: str
    content: Optional[str] = None
    sticker_id: Optional[str] = None
    is_sticker: bool = False
    is_image: bool = False
    created_at: int
    is_read: bool