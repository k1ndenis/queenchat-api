from pydantic import BaseModel
from typing import List, Optional

class PrivateChatRequest(BaseModel):
    username: str

class ChatCreate(BaseModel):
    name: Optional[str] = None
    is_group: bool = False
    chat_type: str = "private"
    participant_ids: List[str]

class GroupChatCreate(BaseModel):
    name: str
    participant_ids: Optional[List[str]] = []

class ParticipantResponse(BaseModel):
    user_id: str
    username: str
    display_name: Optional[str] = None
    avatar: Optional[str] = None
    joined_at: int

class ChatResponse(BaseModel):
    id: str
    name: Optional[str] = None
    avatar: Optional[str] = None
    chat_type: str = "private"
    is_group: bool
    created_by: str
    created_at: int
    updated_at: int
    participants: List[ParticipantResponse] = []
    unread_count: int = 0
    has_unread_reactions: bool = False
    unread_reactions_count: int = 0

class ChatDeleteResponse(BaseModel):
    id: str
    message: str = "Chat deleted successfully"

class ChatUpdate(BaseModel):
    name: Optional[str] = None
    avatar: Optional[str] = None


class ChatBackgroundUpdate(BaseModel):
    background_type: str
    background_value: Optional[str] = None


class ChatBackgroundResponse(BaseModel):
    background_type: str = "default"
    background_value: Optional[str] = None
    updated_at: Optional[int] = None
    updated_by_user_id: Optional[str] = None
