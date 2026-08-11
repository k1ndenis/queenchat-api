from pydantic import BaseModel
from typing import Optional

class TokenResponse(BaseModel):
    token: str
    user: dict

class PhoneRequest(BaseModel):
    phone: str

class SendCodeRequest(BaseModel):
    phone: str

class RegisterRequest(BaseModel):
    phone: str
    username: str
    password: str
    display_name: Optional[str] = None

class LoginRequest(BaseModel):
    phone: str
    password: str