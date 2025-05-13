from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

class AnalysisFindingBase(BaseModel):
    file_path: str = Field(..., max_length=1024)
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    severity: str = Field(..., max_length=50) # Sẽ được validate bởi DB CHECK constraint
    message: str
    suggestion: Optional[str] = None
    agent_name: Optional[str] = Field(None, max_length=100)

class AnalysisFindingCreate(AnalysisFindingBase):
    pass

class AnalysisFindingPublic(AnalysisFindingBase):
    id: int
    pr_analysis_request_id: int
    created_at: datetime

    class Config:
        from_attributes = True