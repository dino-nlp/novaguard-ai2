# novaguard-backend/app/models/full_project_analysis_request_model.py
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func, Enum as SQLAlchemyEnum
from sqlalchemy.orm import relationship
import enum

from app.core.db import Base

class FullProjectAnalysisStatus(enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SOURCE_FETCHED = "source_fetched"
    CKG_BUILDING = "ckg_building"
    ANALYZING = "analyzing"
    COMPLETED = "completed"
    FAILED = "failed"

class FullProjectAnalysisRequest(Base):
    __tablename__ = "fullprojectanalysisrequests"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    branch_name = Column(String(255), nullable=False)

    status = Column(SQLAlchemyEnum(FullProjectAnalysisStatus,
                                name="full_project_analysis_status_enum",
                                create_type=True,
                                values_callable=lambda obj: [e.value for e in obj]),
                    default=FullProjectAnalysisStatus.PENDING,
                    nullable=False)

    error_message = Column(Text, nullable=True)
    # Đây là các trường thời gian
    requested_at = Column(DateTime(timezone=True), server_default=func.now()) # << Thuộc tính này có tồn tại
    started_at = Column(DateTime(timezone=True), nullable=True)
    source_fetched_at = Column(DateTime(timezone=True), nullable=True)
    ckg_built_at = Column(DateTime(timezone=True), nullable=True)
    analysis_completed_at = Column(DateTime(timezone=True), nullable=True)

    total_files_analyzed = Column(Integer, nullable=True)
    total_findings = Column(Integer, nullable=True)

    project = relationship(
        "Project",
        foreign_keys=[project_id],
        back_populates="full_scan_requests"
    )
    
    findings = relationship("AnalysisFinding", back_populates="full_project_analysis_request", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<FullProjectAnalysisRequest(id={self.id}, project_id={self.project_id}, branch='{self.branch_name}', status='{self.status.value}')>"