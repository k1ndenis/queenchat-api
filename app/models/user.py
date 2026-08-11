from pydantic import BaseModel
from typing import Optional

class UserSchema(BaseModel):
    id: str
    username: str
    phone: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    avatar: Optional[str] = None
    created_at: int

class UserProfile(BaseModel):
    id: str
    username: str
    phone: str
    display_name: Optional[str] = None
    email: Optional[str] = None
    avatar: Optional[str] = None
    created_at: int

class UserCreateSchema(BaseModel):
    username: str
    phone: str
    display_name: Optional[str] = None
    email: Optional[str] = None
    avatar: Optional[str] = None

class UpdateProfileRequest(BaseModel):
    username: str = None
    phone: Optional[str] = None
    display_name: Optional[str] = None
    avatar: Optional[str] = None
    email: Optional[str] = None