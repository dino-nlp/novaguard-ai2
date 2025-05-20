# novaguard-backend/app/models/analysis_finding_model.py
from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime, func, Enum as SQLAlchemyEnum, JSON
from sqlalchemy.orm import relationship
from app.core.db import Base
import enum

class PyAnalysisSeverity(str, enum.Enum):
    ERROR = "Error"
    WARNING = "Warning"
    NOTE = "Note"
    INFO = "Info"

class PyScanType(str, enum.Enum):
    PR = "pr"
    FULL_PROJECT = "full_project"
    
class PyFindingLevel(str, enum.Enum):
    FILE = "file"
    MODULE = "module"
    PROJECT = "project"
    
class AnalysisFinding(Base):
    __tablename__ = "analysisfindings"

    id = Column(Integer, primary_key=True, index=True)
    
    # Liên kết với một trong hai loại request
    pr_analysis_request_id = Column(Integer, ForeignKey("pranalysisrequests.id", ondelete="CASCADE"), nullable=True, index=True)
    full_project_analysis_request_id = Column(Integer, ForeignKey("fullprojectanalysisrequests.id", ondelete="CASCADE"), nullable=True, index=True)

    scan_type = Column(SQLAlchemyEnum(PyScanType,
                                       name="scan_type_enum",
                                       create_type=True, # Tạo ENUM type nếu chưa có
                                       values_callable=lambda obj: [e.value for e in obj]),
                       nullable=False)

    file_path = Column(String(1024), nullable=True) # Có thể null cho project-level
    line_start = Column(Integer, nullable=True)
    line_end = Column(Integer, nullable=True)
    severity = Column(SQLAlchemyEnum(PyAnalysisSeverity,
                                    name="analysis_severity_enum",
                                    create_type=False, # Giả sử đã tạo từ schema.sql ban đầu
                                    values_callable=lambda obj: [e.value for e in obj]),
                    nullable=False)
    message = Column(Text, nullable=False)
    suggestion = Column(Text, nullable=True)
    agent_name = Column(String(100), nullable=True)
    code_snippet = Column(Text, nullable=True)
    
    # Các trường mới
    finding_type = Column(String(100), nullable=True, comment="Type of finding, e.g., 'code_smell', 'security_vuln', 'architectural_issue'")
    finding_level = Column(SQLAlchemyEnum(PyFindingLevel,
                                        name="finding_level_enum",
                                        create_type=True,
                                        values_callable=lambda obj: [e.value for e in obj]),
                        nullable=False, default=PyFindingLevel.FILE)
    module_name = Column(String(255), nullable=True, comment="Module name if finding is module-level")
    meta_data = Column(JSON, nullable=True, comment="Additional structured data as JSON")


    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships (cần cập nhật nếu full_project_analysis_request cũng có relationship ngược lại)
    pr_analysis_request = relationship("PRAnalysisRequest", back_populates="findings")
    full_project_analysis_request = relationship("FullProjectAnalysisRequest", back_populates="findings") # Cần thêm "findings" vào FullProjectAnalysisRequest model

    def __repr__(self):
        return f"<AnalysisFinding(id={self.id}, scan_type='{self.scan_type.value if self.scan_type else None}', severity='{self.severity.value if self.severity else None}')>"
