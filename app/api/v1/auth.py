from fastapi import APIRouter, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
import os

from app.services.auth_service import AuthService
from app.dependency import get_db, get_auth_service
from app.core.database import UserORM as User
from app.models.user import UserSchema
from app.models.auth import RegisterRequest, LoginRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])

@router.get("/get_users")
def get_users(auth_service: AuthService = Depends(get_auth_service)) -> list[UserSchema]:
    return auth_service.get_all_users()

@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(
    request: RegisterRequest,
    auth_service: AuthService = Depends(get_auth_service)
) -> TokenResponse:
    return auth_service.register(payload=request)

@router.post("/login", response_model=TokenResponse)
def login(
        request: LoginRequest,
        auth_service: AuthService = Depends(get_auth_service)
    ) -> TokenResponse:
    return auth_service.login(payload=request)

@router.post("/logout")
def logout():
    return {"message": "Logged out successfully"}