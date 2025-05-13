from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from datetime import datetime

# --- User Schemas ---

class UserBase(BaseModel):
    email: EmailStr

class UserCreate(UserBase):
    password: str = Field(..., min_length=8, description="User password, at least 8 characters")

class UserPublic(UserBase):
    id: int
    github_user_id: Optional[str] = None # Thêm để hiển thị nếu đã kết nối GitHub
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True # Pydantic V2, trước là orm_mode = True

# --- Token Schemas ---

class Token(BaseModel):
    access_token: str
    token_type: str # Thường là "bearer"

class TokenPayload(BaseModel):
    sub: Optional[str] = None # 'sub' (subject) thường là user ID hoặc email
    exp: Optional[datetime] = None

# Schema cho form đăng nhập (FastAPI dùng OAuth2PasswordRequestForm, nhưng đây để rõ ràng)
class UserLogin(BaseModel):
    username: EmailStr # Sẽ map email vào đây
    password: str

# Schema cho response sau khi GitHub OAuth thành công
class GitHubOAuthResponse(BaseModel):
    message: str
    access_token: str # NovaGuard's JWT
    token_type: str
    user_info: UserPublic