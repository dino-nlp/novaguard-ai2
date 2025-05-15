# novaguard-backend/app/models/analysis_finding_model.py
from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime, func, Enum as SQLAlchemyEnum
from sqlalchemy.orm import relationship
from app.core.db import Base
import enum

class PyAnalysisSeverity(str, enum.Enum):
    ERROR = "Error"
    WARNING = "Warning"
    NOTE = "Note"
    INFO = "Info"
    
class AnalysisFinding(Base):
    __tablename__ = "analysisfindings" # Trong schema.sql là "analysisfindings" (chữ thường)

    id = Column(Integer, primary_key=True, index=True)
    # Trong schema.sql là "pranalysisrequests"
    pr_analysis_request_id = Column(Integer, ForeignKey("pranalysisrequests.id", ondelete="CASCADE"), nullable=False, index=True)

    file_path = Column(String(1024), nullable=False)
    line_start = Column(Integer, nullable=True) # Trong schema.sql là line_start
    line_end = Column(Integer, nullable=True)   # Trong schema.sql là line_end
    severity = Column(SQLAlchemyEnum(PyAnalysisSeverity, # << SỬ DỤNG PYTHON ENUM Ở ĐÂY
                                    name="analysis_severity_enum", # Tên ENUM type trong DB
                                    create_type=False, # create_type=False vì schema.sql đã tạo nó
                                    values_callable=lambda obj: [e.value for e in obj]),
                    nullable=False)
    message = Column(Text, nullable=False)
    suggestion = Column(Text, nullable=True)
    agent_name = Column(String(100), nullable=True)
    
    code_snippet = Column(Text, nullable=True)  # Đoạn code liên quan đến phát hiện

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationship trong schema.sql không định nghĩa, nhưng model SQLAlchemy có
    pr_analysis_request = relationship("PRAnalysisRequest", back_populates="findings")

    def __repr__(self):
        return f"<AnalysisFinding(id={self.id}, request_id={self.pr_analysis_request_id}, file='{self.file_path}', severity='{self.severity.value if self.severity else None}')>"