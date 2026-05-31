from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import auth
from app.api.v1 import chats
from app.core.database import lifespan
from app.core.redis import redis_client

app = FastAPI(
    title="QueenChat API",
    version="1.0.0",
    contact={"name": "Denis", "email": "k1ndenis.dev@gmail.com"},
    lifespan=lifespan,
    redirect_slashes=False
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(chats.router, prefix="/api/chats", tags=["chats"])