from fastapi import APIRouter, HTTPException, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
import asyncio

from app.services.auth_service import AuthService
from app.core.dependency import get_db, get_auth_service, get_current_user
from app.core.database import UserORM as User  # ← добавить
from app.core.security import create_token  # ← добавить для ws-token
from app.models.auth import RegisterRequest, LoginRequest, TokenResponse

router = APIRouter()

@router.get("/get_users")
def get_users(
    auth_service: AuthService = Depends(get_auth_service)
):
    return auth_service.get_all_users()

@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(
    request: RegisterRequest,
    auth_service: AuthService = Depends(get_auth_service)
):
    result = auth_service.register(payload=request)
    
    response = JSONResponse(content={
        "user": result["user"]
    })
    
    response.set_cookie(
        key="access_token",
        value=result["token"],
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=7 * 24 * 60 * 60,
        path="/",
    )
    
    return response

@router.post("/login")
def login(
    request: LoginRequest,
    auth_service: AuthService = Depends(get_auth_service)
):
    result = auth_service.login(payload=request)
    
    response = JSONResponse(content={
        "user": result["user"]
    })
    
    response.set_cookie(
        key="access_token",
        value=result["token"],
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=7 * 24 * 60 * 60,
        path="/",
    )
    
    return response

@router.get("/me")
def get_me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "username": current_user.username,
        "email": current_user.email
    }

@router.get("/ws-token")
def get_ws_token(current_user: User = Depends(get_current_user)):
    token = create_token(current_user.id, current_user.username)
    return {"token": token}

@router.post("/logout")
def logout():
    response = JSONResponse(content={"message": "Logged out successfully"})
    response.delete_cookie("access_token", path="/")
    return response