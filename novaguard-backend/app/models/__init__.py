# novaguard-backend/app/models/__init__.py
from app.core.db import Base

# Import các class model trực tiếp
from .user_model import User
from .project_model import Project
from .pr_analysis_request_model import PRAnalysisRequest, PRAnalysisStatus
from .analysis_finding_model import AnalysisFinding, PyAnalysisSeverity
from .full_project_analysis_request_model import FullProjectAnalysisRequest, FullProjectAnalysisStatus


__all__ = [
    "Base",
    "User",
    "Project",
    "PRAnalysisRequest",
    "PRAnalysisStatus", # Đảm bảo PRAnalysisStatus cũng được export
    "AnalysisFinding",
    "PyAnalysisSeverity",
    "FullProjectAnalysisRequest",
    "FullProjectAnalysisStatus",
]