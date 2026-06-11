import os
import uuid
from pathlib import Path
from sqlalchemy.orm import Session
from fastapi import UploadFile, HTTPException
from app.repositories.file_repository import FileRepository

UPLOAD_DIR = Path("/app/uploads/images")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

class FileService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = FileRepository(db)
    
    def _validate_file(self, filename: str, size: int) -> None:
        ext = os.path.splitext(filename)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=400, detail="File type not allowed")
        
        if size > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail="File too large (max 10MB)")
    
    def _save_file(self, file: UploadFile, filename: str) -> str:
        file_path = UPLOAD_DIR / filename
        try:
            content = file.file.read()
            with open(file_path, "wb") as f:
                f.write(content)
            return str(file_path)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to save file: {str(e)}")
    
    async def upload_image(self, file: UploadFile, user_id: str, chat_id: str = None) -> dict:
        self._validate_file(file.filename, file.size or 0)
        
        ext = os.path.splitext(file.filename)[1].lower()
        new_filename = f"{uuid.uuid4().hex}{ext}"
        
        file_path = self._save_file(file, new_filename)
        
        file_record = self.repo.create(
            filename=new_filename,
            original_name=file.filename,
            file_path=file_path,
            file_size=file.size or 0,
            mime_type=file.content_type or "image/unknown",
            user_id=user_id,
            chat_id=chat_id
        )
        
        self.db.commit()
        
        file_url = f"/uploads/images/{new_filename}"
        
        return {
            "success": True,
            "id": file_record.id,
            "url": file_url,
            "filename": new_filename,
            "original_name": file.filename,
            "size": file.size,
            "mime_type": file.content_type
        }
    
    def get_file_url(self, filename: str) -> str:
        return f"/uploads/images/{filename}"
    
    def delete_file(self, file_id: str, user_id: str) -> bool:
        file_record = self.repo.get_by_id(file_id)
        if not file_record:
            return False
        
        if os.path.exists(file_record.file_path):
            os.remove(file_record.file_path)
        
        result = self.repo.delete(file_id, user_id)
        self.db.commit()
        
        return result