from sqlalchemy.orm import Session

from app.models import User
from app.auth_service.schemas import UserCreate
from app.core.security import get_password_hash # Đã có từ security.py

def get_user_by_email(db: Session, email: str) -> User | None:
    """
    Lấy một user từ DB bằng địa chỉ email.
    """
    return db.query(User).filter(User.email == email).first()

def get_user_by_id(db: Session, user_id: int) -> User | None:
    """
    Lấy một user từ DB bằng ID.
    """
    return db.query(User).filter(User.id == user_id).first()

def get_user_by_github_id(db: Session, github_id: str) -> User | None:
    """
    Lấy một user từ DB bằng GitHub User ID.
    """
    return db.query(User).filter(User.github_user_id == github_id).first()


def create_user(db: Session, user: UserCreate, is_oauth_user: bool = False) -> User:
    """
    Tạo một user mới trong DB.
    Nếu is_oauth_user là True, password được cung cấp chỉ là placeholder và không dùng để login.
    """
    # Password sẽ luôn được hash, dù là placeholder cho OAuth user
    hashed_password = get_password_hash(user.password)
    
    db_user = User(
        email=user.email,
        password_hash=hashed_password
        # github_user_id và github_access_token_encrypted sẽ được set riêng cho OAuth flow
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

# Bạn có thể thêm các hàm cập nhật user ở đây nếu cần, ví dụ:
# def update_user_github_info(db: Session, db_user: User, github_user_id: str, encrypted_token: str) -> User:
#     db_user.github_user_id = github_user_id
#     db_user.github_access_token_encrypted = encrypted_token
#     db.commit()
#     db.refresh(db_user)
#     return db_user