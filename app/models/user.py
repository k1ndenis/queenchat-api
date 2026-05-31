from pydantic import BaseModel

class UserSchema(BaseModel):
    id: str
    username: str
    email: str
    created_at: int

class UserCreateSchema(BaseModel):
    username: str
    email: str
    password: str

class UserDeleteSchema(BaseModel):
    id: str