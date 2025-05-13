from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func, Enum as SQLAlchemyEnum
from sqlalchemy.orm import relationship
import enum

from app.core.db import Base
# from app.models.project_model import Project # Sẽ được dùng cho relationship

class PRAnalysisStatus(enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DATA_FETCHED = "data_fetched"
    COMPLETED = "completed"
    FAILED = "failed"

class PRAnalysisRequest(Base):
    __tablename__ = "pranalysisrequests" # Tên bảng đã định nghĩa trong schema.sql

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    
    pr_number = Column(Integer, nullable=False)
    pr_title = Column(Text, nullable=True)
    pr_github_url = Column(String(2048), nullable=True)
    head_sha = Column(String(40), nullable=True) # SHA của commit cuối cùng trong PR
    
    # Sử dụng SQLAlchemy Enum để ràng buộc giá trị của status
    status = Column(SQLAlchemyEnum(PRAnalysisStatus, name="pr_analysis_status_enum", create_type=True), 
                    default=PRAnalysisStatus.PENDING, 
                    nullable=False)
    
    error_message = Column(Text, nullable=True) # Thông báo lỗi nếu failed
    
    requested_at = Column(DateTime(timezone=True), server_default=func.now()) # Thời điểm webhook được nhận
    started_at = Column(DateTime(timezone=True), nullable=True) # Thời điểm worker bắt đầu xử lý
    completed_at = Column(DateTime(timezone=True), nullable=True) # Thời điểm worker hoàn thành

    # Mối quan hệ với Project (nhiều PRAnalysisRequest thuộc về một Project)
    project = relationship("Project", back_populates="pr_analysis_requests")
    findings = relationship("AnalysisFinding", back_populates="pr_analysis_request", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<PRAnalysisRequest(id={self.id}, project_id={self.project_id}, pr_number={self.pr_number}, status='{self.status.value}')>"