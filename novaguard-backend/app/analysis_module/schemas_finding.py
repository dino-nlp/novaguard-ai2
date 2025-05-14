# novaguard-backend/app/analysis_module/schemas_finding.py
from pydantic import BaseModel, Field
from typing import Optional, List # Đảm bảo List đã được import
from datetime import datetime

# Import các schema cần thiết từ các module khác
from app.webhook_service.schemas_pr_analysis import PRAnalysisRequestPublic # << IMPORT SCHEMA NÀY

class AnalysisFindingBase(BaseModel):
    file_path: str = Field(..., max_length=1024)
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    severity: str = Field(..., max_length=50)
    message: str
    suggestion: Optional[str] = None
    agent_name: Optional[str] = Field(None, max_length=100)
    code_snippet: Optional[str] = None

class AnalysisFindingCreate(AnalysisFindingBase):
    pass

class AnalysisFindingPublic(AnalysisFindingBase):
    id: int
    pr_analysis_request_id: int # Giữ lại để có thể query ngược nếu cần, dù không hiển thị trực tiếp trong mọi view
    created_at: datetime

    class Config:
        from_attributes = True


class PRAnalysisReportDetail(BaseModel):
    pr_request_details: PRAnalysisRequestPublic  # Chi tiết của PRAnalysisRequest
    findings: List[AnalysisFindingPublic]       # Danh sách các phát hiện

    class Config:
        from_attributes = True
