from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from typing import Optional
from pathlib import Path
import os
import uuid
import traceback

from app.core.dependency import get_db, get_current_user
from app.core.database import UserORM as User
from app.services.file_service import FileService

router = APIRouter()

@router.post("/upload-image/{chat_id}")
async def upload_image(
    chat_id: str,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        print(f"📸 === UPLOAD IMAGE ===")
        print(f"📸 Chat ID: {chat_id}")
        print(f"📸 User ID: {current_user.id}")
        print(f"📸 Filename: {file.filename}")
        print(f"📸 Content-Type: {file.content_type}")
        
        # Проверяем файл
        content = await file.read()
        print(f"📸 File size: {len(content)} bytes")
        
        if not content:
            raise HTTPException(status_code=400, detail="Empty file")
        
        # Сохраняем
        ext = os.path.splitext(file.filename)[1].lower()
        new_filename = f"{uuid.uuid4().hex}{ext}"
        
        upload_dir = Path("/app/uploads/images")
        upload_dir.mkdir(parents=True, exist_ok=True)
        
        file_path = upload_dir / new_filename
        with open(file_path, "wb") as f:
            f.write(content)
        
        file_url = f"/uploads/images/{new_filename}"
        
        # TODO: сохранить в БД через FileService
        
        return {
            "success": True,
            "url": file_url,
            "filename": new_filename,
            "size": len(content)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Upload error: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))