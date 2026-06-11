from sqlalchemy import create_engine, String, ForeignKey, Column, Integer, Boolean
from sqlalchemy.orm import sessionmaker, DeclarativeBase, Mapped, mapped_column, relationship
import uuid
import os
import time
import asyncio
from dotenv import load_dotenv
from contextlib import asynccontextmanager
from fastapi import FastAPI

from app.core.redis import redis_client

load_dotenv()

TESTING = os.getenv("TESTING") == "true"

if TESTING:
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./test.db")
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    DB_HOST = os.getenv("DB_HOST")
    DB_PORT = os.getenv("DB_PORT")
    DB_NAME = os.getenv("DB_NAME")
    DB_USER = os.getenv("DB_USER")
    DB_PASSWORD = os.getenv("DB_PASSWORD")
    DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class Base(DeclarativeBase):
    id: Mapped[str] = mapped_column(primary_key=True, default=lambda: str(uuid.uuid4()))

class UserORM(Base):
    __tablename__ = "users"
    
    email: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    username: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[int] = mapped_column(nullable=False)

    chats: Mapped[list["ChatORM"]] = relationship(
        secondary="chat_participants",
        back_populates="participants"
    )
    messages: Mapped[list["MessageORM"]] = relationship("MessageORM", foreign_keys="[MessageORM.sender_id]")

class ChatORM(Base):
    __tablename__ = "chats"

    name: Mapped[str] = mapped_column(String, unique=False, nullable=True)
    is_group: Mapped[bool] = mapped_column(default=False)
    created_by: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[int] = mapped_column(default=lambda: int(time.time()))
    updated_at: Mapped[int] = mapped_column(default=lambda: int(time.time()), onupdate=lambda: int(time.time()))
    
    participants: Mapped[list["UserORM"]] = relationship(
        secondary="chat_participants",
        back_populates="chats"
    )
    messages: Mapped[list["MessageORM"]] = relationship(
        "MessageORM",
        back_populates="chat",
        cascade="all, delete-orphan"
    )

class ChatParticipantORM(Base):
    __tablename__ = "chat_participants"
    
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    joined_at: Mapped[int] = mapped_column(default=lambda: int(time.time()))

class MessageORM(Base):
    __tablename__ = "messages"
    
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id"))
    sender_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    content: Mapped[str] = mapped_column(String, nullable=True)
    sticker_id: Mapped[str] = mapped_column(String, nullable=True)
    is_sticker: Mapped[bool] = mapped_column(Boolean, default=False)
    is_image: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time()))
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    
    chat: Mapped["ChatORM"] = relationship("ChatORM", back_populates="messages")
    sender: Mapped["UserORM"] = relationship("UserORM", foreign_keys=[sender_id], overlaps="messages")

class NotificationORM(Base):
    __tablename__ = "notifications"
    
    id: Mapped[str] = mapped_column(primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id"), nullable=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, default="info")
    is_read: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[int] = mapped_column(default=lambda: int(time.time()))
    
    user: Mapped["UserORM"] = relationship("UserORM", foreign_keys=[user_id])

class FileORM(Base):
    __tablename__ = "files"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    filename = Column(String, nullable=False)
    original_name = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    file_size = Column(Integer, nullable=False)
    mime_type = Column(String, nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    chat_id = Column(String, ForeignKey("chats.id"), nullable=True)
    created_at = Column(Integer, nullable=False, default=lambda: int(time.time()))
    
    user = relationship("UserORM", back_populates="files")
    chat = relationship("ChatORM", back_populates="files")

UserORM.files = relationship("FileORM", back_populates="user", cascade="all, delete-orphan")

ChatORM.files = relationship("FileORM", back_populates="chat", cascade="all, delete-orphan")

async def cleanup_old_notifications():
    while True:
        try:
            await asyncio.sleep(86400)
            
            db = SessionLocal()
            try:
                cutoff_time = int(time.time()) - (30 * 86400)
                deleted = db.query(NotificationORM).filter(
                    NotificationORM.created_at < cutoff_time
                ).delete()
                db.commit()
                
                if deleted > 0:
                    print(f"✅ Cleaned {deleted} old notifications")
            except Exception as e:
                print(f"❌ Cleanup error: {e}")
                db.rollback()
            finally:
                db.close()
        except Exception as e:
            print(f"❌ Cleanup task error: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    
    if not TESTING:
        try:
            redis_client.ping()
            print("✅ Redis connected")
        except Exception as e:
            print(f"❌ Redis connection failed: {e}")
    
    if not TESTING:
        cleanup_task = asyncio.create_task(cleanup_old_notifications())
        print("✅ Notification cleanup task started (runs every 24 hours)")
    else:
        cleanup_task = None
    
    yield
    
    if cleanup_task:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
    
    if not TESTING:
        redis_client.close()
        print("Redis connection closed")