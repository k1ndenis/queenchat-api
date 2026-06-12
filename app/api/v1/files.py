from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from typing import List, Optional
from sqlalchemy.orm import Session
from pathlib import Path
import os
import uuid
import json

from app.core.dependency import get_db, get_current_user
from app.core.database import UserORM as User

router = APIRouter()

UPLOAD_DIR = Path("/app/uploads/images")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_FILES = 10

@router.post("/upload-images")
async def upload_images(
    files: List[UploadFile] = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Maximum {MAX_FILES} files allowed")
    
    uploaded_urls = []
    errors = []
    
    for file in files:
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            errors.append(f"{file.filename}: unsupported file type")
            continue
        
        file.file.seek(0, 2)
        size = file.file.tell()
        file.file.seek(0)
        if size > MAX_FILE_SIZE:
            errors.append(f"{file.filename}: file too large (max 10MB)")
            continue
        
        new_filename = f"{uuid.uuid4().hex}{ext}"
        file_path = UPLOAD_DIR / new_filename
        
        content = await file.read()
        with open(file_path, "wb") as f:
            f.write(content)
        
        file_url = f"/uploads/images/{new_filename}"
        uploaded_urls.append(file_url)
    
    return {
        "success": True,
        "urls": uploaded_urls,
        "errors": errors if errors else None,
        "count": len(uploaded_urls)
    }

@router.post("/upload-avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not file.content_type.startswith('image/'):
        raise HTTPException(status_code=400, detail="File must be an image")
    
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)
    if size > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File too large (max 10MB)")
    
    ext = os.path.splitext(file.filename)[1].lower()
    new_filename = f"avatar_{current_user.id}{ext}"
    file_path = UPLOAD_DIR / new_filename
    
    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)
    
    avatar_url = f"/uploads/images/{new_filename}"
    
    from app.services.auth_service import AuthService
    auth_service = AuthService(db)
    auth_service.update_profile(
        user_id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        avatar=avatar_url
    )
    db.commit()
    
    return {"success": True, "url": avatar_url}