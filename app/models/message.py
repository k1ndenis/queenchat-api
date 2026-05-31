from pydantic import BaseModel

class MessageCreate(BaseModel):
    content: str

class MessageResponse(BaseModel):
    id: str
    chat_id: str
    sender_id: str
    content: str
    created_at: int
    is_read: bool