from sqlalchemy import Column, Integer, String, Text, DateTime, func
from sqlalchemy.orm import relationship

from app.core.db import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(Text, nullable=False) # Sẽ là placeholder cho OAuth users
    
    github_user_id = Column(String(255), unique=True, nullable=True, index=True)
    github_access_token_encrypted = Column(Text, nullable=True) # Mã hóa bằng Fernet
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), default=func.now(), onupdate=func.now())

    # Mối quan hệ với bảng Project (one-to-many: một User có nhiều Project)
    # 'Project' là tên class của model Project
    projects = relationship("Project", back_populates="owner", cascade="all, delete-orphan")


    def __repr__(self):
        return f"<User(id={self.id}, email='{self.email}')>"