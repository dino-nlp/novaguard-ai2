# novaguard-backend/app/models/full_project_analysis_request_model.py
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func, Enum as SQLAlchemyEnum
from sqlalchemy.orm import relationship
import enum

from app.core.db import Base

class FullProjectAnalysisStatus(enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"           # Đang lấy source code hoặc chuẩn bị
    SOURCE_FETCHED = "source_fetched"   # Đã lấy source code xong
    CKG_BUILDING = "ckg_building"       # Đang xây dựng/cập nhật CKG
    ANALYZING = "analyzing"             # Đang chạy các agents phân tích
    COMPLETED = "completed"
    FAILED = "failed"

class FullProjectAnalysisRequest(Base):
    __tablename__ = "fullprojectanalysisrequests"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    branch_name = Column(String(255), nullable=False)
    # ... (các trường status, error_message, times...)

    total_files_analyzed = Column(Integer, nullable=True)
    total_findings = Column(Integer, nullable=True) # Tổng số phát hiện cho scan này

    project = relationship(
        "Project",
        foreign_keys=[project_id],
        back_populates="full_scan_requests"
    )
    
    # Thêm relationship này
    findings = relationship("AnalysisFinding", back_populates="full_project_analysis_request", cascade="all, delete-orphan")


    def __repr__(self):
        return f"<FullProjectAnalysisRequest(id={self.id}, project_id={self.project_id}, branch='{self.branch_name}', status='{self.status.value}')>"