from pydantic import BaseModel
from typing import Optional, List

class MessageCreate(BaseModel):
    content: str
    is_image: bool = False
    reply_to_id: Optional[str] = None
    images: Optional[List[str]] = None

class MessageResponse(BaseModel):
    id: str
    chat_id: str
    sender_id: str
    content: Optional[str] = None
    sticker_id: Optional[str] = None
    is_sticker: bool = False
    is_image: bool = False
    images: Optional[List[str]] = None
    reply_to_id: Optional[str] = None
    reply_to_message: Optional["MessageResponse"] = None
    created_at: int
    is_read: bool

MessageResponse.model_rebuild()