"""Authentication Pydantic v2 schemas."""

from datetime import datetime
from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserRegister(BaseModel):
    """User registration schema."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128, description="User password (min 8 characters)")


class UserLogin(BaseModel):
    """User login request schema."""

    email: EmailStr
    password: str


class UserResponse(BaseModel):
    """User response schema."""

    id: int
    email: EmailStr
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    """JWT Token response schema."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int


class TokenPayload(BaseModel):
    """Decoded JWT payload."""

    sub: str
    exp: int
