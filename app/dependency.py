from fastapi import Depends
from sqlalchemy.orm import Session
from typing import Generator
import os

from app.database import SessionLocal

def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()