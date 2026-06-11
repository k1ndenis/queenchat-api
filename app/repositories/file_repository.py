from sqlalchemy.orm import Session
from app.core.database import FileORM
import uuid
import time

class FileRepository:
    def __init__(self, db: Session):
        self.db = db
    
    def create(self, filename: str, original_name: str, file_path: str, file_size: int, mime_type: str, user_id: str, chat_id: str = None) -> FileORM:
        file = FileORM(
            id=str(uuid.uuid4()),
            filename=filename,
            original_name=original_name,
            file_path=file_path,
            file_size=file_size,
            mime_type=mime_type,
            user_id=user_id,
            chat_id=chat_id,
            created_at=int(time.time())
        )
        self.db.add(file)
        self.db.flush()
        return file
    
    def get_by_id(self, file_id: str) -> FileORM | None:
        return self.db.query(FileORM).filter(FileORM.id == file_id).first()
    
    def get_by_chat(self, chat_id: str, limit: int = 50, offset: int = 0) -> list[FileORM]:
        return self.db.query(FileORM).filter(
            FileORM.chat_id == chat_id
        ).order_by(
            FileORM.created_at.desc()
        ).offset(offset).limit(limit).all()
    
    def delete(self, file_id: str, user_id: str) -> bool:
        file = self.db.query(FileORM).filter(
            FileORM.id == file_id,
            FileORM.user_id == user_id
        ).first()
        if file:
            self.db.delete(file)
            self.db.flush()
            return True
        return False
    
    def delete_by_chat(self, chat_id: str) -> None:
        self.db.query(FileORM).filter(FileORM.chat_id == chat_id).delete(synchronize_session=False)