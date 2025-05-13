from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from typing import Generator

from app.core.config import settings # Import settings từ config.py

# SQLAlchemy engine
engine = create_engine(
    settings.DATABASE_URL,
    # pool_pre_ping=True, # Có thể bật để kiểm tra kết nối trước mỗi lần checkout từ pool
    # connect_args={"check_same_thread": False} # Chỉ cần cho SQLite, không cần cho PostgreSQL
)

# SessionLocal factory để tạo DB sessions
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class cho các model SQLAlchemy
Base = declarative_base()

# Dependency để inject DB session vào API endpoints
def get_db() -> Generator:
    db = None
    try:
        db = SessionLocal()
        yield db
    finally:
        if db:
            db.close()

if __name__ == "__main__":
    # Một đoạn test nhỏ để kiểm tra kết nối DB
    try:
        # Thử tạo một session và query đơn giản (sẽ không hoạt động nếu chưa có bảng nào)
        with SessionLocal() as session:
            result = session.execute(text("SELECT 1")) # Cần import text từ sqlalchemy
            print("DB Connection Test (SELECT 1):", result.scalar_one())
        print("Database connection successful and SessionLocal is working.")
    except Exception as e:
        print(f"Database connection failed: {e}")
        print(f"Please ensure your PostgreSQL server is running at: {settings.DATABASE_URL}")
        print("And that the database 'novaguard_db' exists with user 'novaguard_user'.")

    # Để chạy đoạn test trên, bạn cần import text:
    # from sqlalchemy import text