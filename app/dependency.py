from fastapi import Depends
from sqlalchemy.orm import Session
from typing import Generator
import os

from app.core.database import SessionLocal
from app.services.auth_service import AuthService

def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_auth_service(db: Session = Depends(get_db)) -> AuthService:
    return AuthService(
        db=db
    )