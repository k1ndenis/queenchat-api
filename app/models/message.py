from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Optional, List

ALLOWED_REACTIONS = ("👍", "❤️", "😂", "😮", "😢", "🔥")


class MessageReactionSet(BaseModel):
    emoji: str


class MessageForwardRequest(BaseModel):
    target_chat_id: str


class MessageReactionSummary(BaseModel):
    emoji: str
    count: int
    reacted_by_me: bool


class MessageUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(default="", max_length=4000)


class MessageCreate(BaseModel):
    content: Optional[str] = None
    is_image: bool = False
    reply_to_id: Optional[str] = None
    images: Optional[List[str]] = None
    media: Optional[dict] = None

    @model_validator(mode="after")
    def content_or_images_required(self):
        has_content = bool((self.content or "").strip())
        has_images = bool(self.images)
        if not has_content and not has_images and not self.media:
            raise ValueError("Message must contain content or images")
        return self

class MessageResponse(BaseModel):
    id: str
    chat_id: str
    sender_id: str
    content: Optional[str] = None
    sticker_id: Optional[str] = None
    is_sticker: bool = False
    is_image: bool = False
    images: Optional[List[str]] = None
    media: Optional[dict] = None
    reply_to_id: Optional[str] = None
    reply_to_message: Optional["MessageResponse"] = None
    forwarded_from_message_id: Optional[str] = None
    forwarded_from_user_id: Optional[str] = None
    forwarded_from_user_name: Optional[str] = None
    created_at: int
    edited_at: Optional[int] = None
    deleted_at: Optional[int] = None
    is_read: bool
    reactions: List[MessageReactionSummary] = Field(default_factory=list)
    comments_count: int = 0

class CommentCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)

class CommentUpdate(CommentCreate):
    pass

MessageResponse.model_rebuild()
