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
    # Đây là khóa ngoại mà relationship "project" nên sử dụng
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    branch_name = Column(String(255), nullable=False)

    status = Column(SQLAlchemyEnum(FullProjectAnalysisStatus,
                                name="full_project_analysis_status_enum", # Đảm bảo tên này khớp với DB
                                create_type=True, # Sẽ tạo ENUM type nếu chưa có
                                values_callable=lambda obj: [e.value for e in obj]),
                    default=FullProjectAnalysisStatus.PENDING,
                    nullable=False)

    error_message = Column(Text, nullable=True)
    requested_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    source_fetched_at = Column(DateTime(timezone=True), nullable=True)
    ckg_built_at = Column(DateTime(timezone=True), nullable=True)
    analysis_completed_at = Column(DateTime(timezone=True), nullable=True)

    total_files_analyzed = Column(Integer, nullable=True)
    total_findings = Column(Integer, nullable=True)

    # Sửa lại relationship ở đây
    project = relationship(
        "Project",
        foreign_keys=[project_id], # Chỉ định rõ cột khóa ngoại để join
        back_populates="full_scan_requests"  # Thêm back_populates nếu Project có một list các full_scan_requests
    )
    # Nếu bạn muốn có một list các findings cho full scan:
    # full_scan_findings = relationship("FullProjectAnalysisFinding", back_populates="full_project_analysis_request")

    def __repr__(self):
        return f"<FullProjectAnalysisRequest(id={self.id}, project_id={self.project_id}, branch='{self.branch_name}', status='{self.status.value}')>"
