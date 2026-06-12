from pydantic import BaseModel
from typing import Optional

class UserSchema(BaseModel):
    id: str
    username: str
    email: str
    avatar: Optional[str] = None
    created_at: int

class UserProfile(BaseModel):
    id: str
    username: str
    email: str
    avatar: Optional[str] = None
    created_at: int

class UserCreateSchema(BaseModel):
    username: str
    email: str
    avatar: Optional[str] = None
    password: str

class UserDeleteSchema(BaseModel):
    id: str

class UpdateProfileRequest(BaseModel):
    username: str
    email: str
    avatar: Optional[str] = None