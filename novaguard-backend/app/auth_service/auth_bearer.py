from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer # Dùng để lấy token từ header
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import decode_access_token
from app.auth_service import crud_user # Để lấy user từ DB
from app.auth_service.schemas import UserPublic # Schema để trả về

# OAuth2PasswordBearer trỏ đến URL login (nơi token được cấp)
# Chú ý: tokenUrl không thực sự gọi đến endpoint này, nó chỉ dùng cho tài liệu Swagger UI
# để biết cách lấy token.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login") 

async def get_current_user(
    token: str = Depends(oauth2_scheme), 
    db: Session = Depends(get_db)
) -> UserPublic: # Trả về UserPublic để không lộ password_hash
    """
    Dependency để lấy user hiện tại từ JWT token.
    - Giải mã token.
    - Lấy user từ DB dựa trên subject (email) trong token.
    - Trả về User object (hoặc UserPublic schema) nếu hợp lệ, ngược lại raise HTTPException.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    payload = decode_access_token(token)
    if payload is None:
        raise credentials_exception
    
    email: str | None = payload.get("sub")
    if email is None:
        raise credentials_exception
    
    user = crud_user.get_user_by_email(db, email=email)
    if user is None:
        raise credentials_exception
        
    return UserPublic.model_validate(user) # Chuyển đổi SQLAlchemy model sang Pydantic schema

async def get_current_active_user( # Có thể mở rộng sau này để kiểm tra user có active không
    current_user: UserPublic = Depends(get_current_user)
) -> UserPublic:
    # if not current_user.is_active: # Giả sử có trường is_active trong model User
    #     raise HTTPException(status_code=400, detail="Inactive user")
    return current_user