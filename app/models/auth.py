from pydantic import BaseModel
from typing import Optional

class TokenResponse(BaseModel):
    token: str
    user: dict

class PhoneRequest(BaseModel):
    phone: str

class SendCodeRequest(BaseModel):
    phone: str
    captcha_token: str

class RegisterRequest(BaseModel):
    phone: str
    username: str
    password: str
    code: str
    captcha_token: str

class LoginRequest(BaseModel):
    phone: str
    password: str
    captcha_token: Optional[str] = None

class ForgotPasswordRequest(BaseModel):
    phone: str
    captcha_token: str

class VerifyCodeRequest(BaseModel):
    phone: str
    code: str

class ResetPasswordRequest(BaseModel):
    phone: str
    code: str
    new_password: str