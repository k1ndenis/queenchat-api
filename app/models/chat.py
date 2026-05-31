from pydantic import BaseModel
from typing import List, Optional

class ChatCreate(BaseModel):
    name: Optional[str] = None
    is_group: bool = False
    participant_ids: List[str]

class ParticipantResponse(BaseModel):
    user_id: str
    username: str
    joined_at: int

class ChatResponse(BaseModel):
    id: str
    name: Optional[str] = None
    is_group: bool
    created_by: str
    created_at: int
    updated_at: int
    participants: List[ParticipantResponse] = []