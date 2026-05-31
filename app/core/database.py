from sqlalchemy import create_engine, String, Boolean, ForeignKey
from sqlalchemy.orm import sessionmaker, DeclarativeBase, Mapped, mapped_column, relationship
import uuid
import os
import time
from dotenv import load_dotenv
from contextlib import asynccontextmanager
from fastapi import FastAPI

load_dotenv()

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
    
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id"))
    sender_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    content: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[int] = mapped_column(default=lambda: int(time.time()))
    is_read: Mapped[bool] = mapped_column(default=False)
    
    chat: Mapped["ChatORM"] = relationship("ChatORM", back_populates="messages")
    sender: Mapped["UserORM"] = relationship("UserORM", foreign_keys=[sender_id], overlaps="messages")

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield