import logging
import os
from fastapi import APIRouter, HTTPException, Depends, status, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.services.auth_service import AuthService
from app.core.dependency import get_db, get_auth_service, get_current_user
from app.core.database import UserORM as User
from app.core.security import create_ws_token
from app.core.rate_limit import (LOGIN_ACCOUNT_FAILURE, LOGIN_IP,
    REGISTER_IP_BURST, REGISTER_IP_HOUR, clear, client_ip, hit, count)
from app.services.captcha_service import captcha_service
from app.models.user import UserProfile, UpdateProfileRequest
from app.models.auth import (
    RegisterRequest,
    LoginRequest,
    TokenResponse
)
from app.core.telemetry import LOGIN_FAILED, LOGIN_SUCCESS, USERS_REGISTERED

router = APIRouter()
logger = logging.getLogger(__name__)

def _cookie(response: JSONResponse, token: str) -> None:
    response.set_cookie(key="access_token", value=token, httponly=True,
        secure=os.getenv("COOKIE_SECURE", "true").lower() == "true",
        samesite="lax", max_age=7 * 24 * 60 * 60, path="/")


@router.get("/get_users")
def get_users(
    auth_service: AuthService = Depends(get_auth_service),
    current_user: User = Depends(get_current_user)
):
    return auth_service.get_all_users(exclude_current=True, current_user_id=current_user.id)


@router.get("/user/{username}")
def get_user_profile(
    username: str,
    current_user: User = Depends(get_current_user),
    auth_service: AuthService = Depends(get_auth_service)
):
    user_profile = auth_service.get_user_profile(username)
    
    if not user_profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="User not found"
        )
    
    return user_profile


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=TokenResponse)
def register(
    request: RegisterRequest,
    http_request: Request,
    auth_service: AuthService = Depends(get_auth_service)
):
    ip = client_ip(http_request)
    hit(REGISTER_IP_HOUR, ip)
    hit(REGISTER_IP_BURST, ip)
    if not captcha_service.verify(request.turnstile_token or ""):
        logger.warning("CAPTCHA_FAILED flow=register")
        raise HTTPException(400, "Security challenge failed. Please try again.")
    result = auth_service.register(request.phone, request.username, request.password)
    
    response = JSONResponse(content={
        "token": result["token"],
        "user": result["user"]
    })
    
    _cookie(response, result["token"])
    USERS_REGISTERED.inc()
    return response


@router.post("/login", response_model=TokenResponse)
def login(
    request: LoginRequest,
    http_request: Request,
    auth_service: AuthService = Depends(get_auth_service)
):
    ip = client_ip(http_request)
    hit(LOGIN_IP, ip)
    challenge_needed = count(LOGIN_ACCOUNT_FAILURE, request.phone) >= 3
    if challenge_needed and not captcha_service.verify(request.turnstile_token or ""):
        logger.warning("CAPTCHA_FAILED flow=login")
        raise HTTPException(400, "Security challenge required", headers={"X-QueenChat-Challenge": "turnstile"})
    try:
        result = auth_service.login(request.phone, request.password)
    except HTTPException as exc:
        if exc.status_code == 401:
            hit(LOGIN_ACCOUNT_FAILURE, request.phone)
            LOGIN_FAILED.inc()
            logger.warning("LOGIN_FAILED")
            # Keep wrong-user and wrong-password responses identical.
            raise HTTPException(401, "Invalid phone or password", headers={"X-QueenChat-Challenge": "turnstile" if challenge_needed else ""})
        raise
    clear(LOGIN_ACCOUNT_FAILURE, request.phone)
    
    response = JSONResponse(content={
        "token": result["token"],
        "user": result["user"]
    })
    
    _cookie(response, result["token"])
    LOGIN_SUCCESS.inc()
    
    return response


@router.get("/me")
def get_me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "username": current_user.username,
        "phone": current_user.phone,
        "display_name": current_user.display_name,
        "email": current_user.email,
        "avatar": current_user.avatar,
        "created_at": current_user.created_at,
        "role": current_user.role,
        "is_blocked": current_user.is_blocked,
    }


@router.get("/ws-token")
def get_ws_token(current_user: User = Depends(get_current_user)):
    token = create_ws_token(current_user.id, current_user.username)
    return {"token": token}


@router.post("/logout")
def logout():
    response = JSONResponse(content={"message": "Logged out successfully"})
    response.delete_cookie("access_token", path="/")
    return response


@router.patch("/profile", response_model=UserProfile)
def update_profile(
    request: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    service = AuthService(db)
    
    if request.username is not None and request.username != current_user.username:
        existing = service.repository.get_by_username(request.username)
        if existing:
            raise HTTPException(status_code=400, detail="Username already taken")
    
    if request.phone is not None and request.phone != current_user.phone:
        existing = service.repository.get_by_phone(request.phone)
        if existing:
            raise HTTPException(status_code=400, detail="Phone already taken")
    
    if request.email is not None and request.email != current_user.email:
        existing = service.repository.get_by_email(request.email)
        if existing:
            raise HTTPException(status_code=400, detail="Email already taken")
    
    user = service.update_profile(
        user_id=current_user.id,
        username=request.username if request.username is not None else current_user.username,
        phone=request.phone,
        avatar=request.avatar,
        display_name=request.display_name,
        email=request.email
    )
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return UserProfile(
        id=user.id,
        username=user.username,
        phone=user.phone,
        display_name=user.display_name,
        email=user.email,
        avatar=user.avatar,
        created_at=user.created_at
    )


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
