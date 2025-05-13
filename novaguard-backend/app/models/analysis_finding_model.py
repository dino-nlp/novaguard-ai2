from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime, func
from sqlalchemy.orm import relationship
from app.core.db import Base

class AnalysisFinding(Base):
    __tablename__ = "analysisfindings"

    id = Column(Integer, primary_key=True, index=True)
    pr_analysis_request_id = Column(Integer, ForeignKey("pranalysisrequests.id", ondelete="CASCADE"), nullable=False, index=True)

    file_path = Column(String(1024), nullable=False)
    line_start = Column(Integer, nullable=True)
    line_end = Column(Integer, nullable=True)
    severity = Column(String(50), nullable=False) # Ví dụ: 'Error', 'Warning', 'Note', 'Info'
    message = Column(Text, nullable=False)
    suggestion = Column(Text, nullable=True)
    agent_name = Column(String(100), nullable=True) # Ví dụ: 'DeepLogicBugHunterAI_MVP1'
    # user_feedback = Column(String(50), nullable=True) # Cho MVP sau

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    pr_analysis_request = relationship("PRAnalysisRequest", back_populates="findings")

    def __repr__(self):
        return f"<AnalysisFinding(id={self.id}, request_id={self.pr_analysis_request_id}, file='{self.file_path}')>"