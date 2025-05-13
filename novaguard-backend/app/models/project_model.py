from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func, UniqueConstraint
from sqlalchemy.orm import relationship

from app.core.db import Base

class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True) # Khóa ngoại tới bảng Users
    
    github_repo_id = Column(String(255), nullable=False) # Sẽ được lấy từ GitHub sau này
    repo_name = Column(String(255), nullable=False) # Tên repo, ví dụ: "owner/repo_name"
    main_branch = Column(String(255), nullable=False) # Nhánh chính, ví dụ: "main", "master"
    
    language = Column(String(100), nullable=True) # Ngôn ngữ lập trình chính của dự án
    custom_project_notes = Column(Text, nullable=True) # Ghi chú tùy chỉnh
    github_webhook_id = Column(String(255), nullable=True) # ID của webhook trên GitHub

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), default=func.now(), onupdate=func.now())

    # Mối quan hệ với User (many-to-one: nhiều Project thuộc về một User)
    # "owner" sẽ là một thuộc tính của Project instance, cho phép truy cập User sở hữu project này.
    # back_populates="projects" sẽ liên kết ngược lại với thuộc tính 'projects' trong model User.
    owner = relationship("User", back_populates="projects")

    # Mối quan hệ với PRAnalysisRequest (one-to-many: một Project có nhiều PRAnalysisRequest)
    pr_analysis_requests = relationship("PRAnalysisRequest", back_populates="project", cascade="all, delete-orphan")


    # Ràng buộc duy nhất: một user không thể thêm cùng một github_repo_id nhiều lần
    __table_args__ = (UniqueConstraint('user_id', 'github_repo_id', name='_user_repo_uc'),)

    def __repr__(self):
        return f"<Project(id={self.id}, name='{self.repo_name}', user_id={self.user_id})>"