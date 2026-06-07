from fastapi import APIRouter, HTTPException, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.services.auth_service import AuthService
from app.core.dependency import get_db, get_auth_service, get_current_user
from app.core.database import UserORM as User
from app.core.security import create_token
from app.models.auth import RegisterRequest, LoginRequest

router = APIRouter()

class ProfileUpdate(BaseModel):
    username: str
    email: str

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
        "email": current_user.email,
        "created_at": current_user.created_at
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

@router.patch("/profile")
def update_profile(
    profile_data: ProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    from app.repositories.auth_repository import AuthRepository
    
    auth_repo = AuthRepository(db)
    
    existing_user = auth_repo.get_by_username(profile_data.username)
    if existing_user and existing_user.id != current_user.id:
        raise HTTPException(status_code=400, detail="Username already taken")
    
    existing_email = auth_repo.get_by_email(profile_data.email)
    if existing_email and existing_email.id != current_user.id:
        raise HTTPException(status_code=400, detail="Email already taken")
    
    current_user.username = profile_data.username
    current_user.email = profile_data.email
    db.commit()
    db.refresh(current_user)
    
    return {
        "id": current_user.id,
        "username": current_user.username,
        "email": current_user.email,
        "created_at": current_user.created_at
    }

@router.delete("/me", status_code=204)
def delete_account(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    service = AuthService(db)
    success = service.delete_user(current_user.id)
    
    if not success:
        raise HTTPException(status_code=400, detail="Failed to delete account")
    
    return None