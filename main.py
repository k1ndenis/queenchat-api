from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import auth
from app.database import lifespan

app = FastAPI(
    title="QueenChat API",
    version="1.0.0",
    contact={"name": "Denis", "email": "k1ndenis.dev@gmail.com"},
    lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api", tags=["auth"])

@app.get("/")
def hello():
    return {"message": "hello"}