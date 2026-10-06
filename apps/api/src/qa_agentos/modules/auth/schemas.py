import uuid
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    email: str
    password: str


class UserCreate(BaseModel):
    email: str
    password: str
    full_name: Optional[str] = None
    role: str = "owner"


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    full_name: Optional[str] = None
    role: str
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class Identity(BaseModel):
    user_id: Optional[uuid.UUID] = None
    email: Optional[str] = None
    role: Optional[str] = None
    project_id: Optional[uuid.UUID] = None
    api_key_id: Optional[uuid.UUID] = None
    scopes: List[str] = []
    is_api_key: bool = False
