# novaguard-backend/app/webhook_service/__init__.py

from .api import router as webhook_router # Import router từ api.py và đổi tên
from . import crud_pr_analysis           # Import các module khác nếu cần
from . import schemas_pr_analysis

# Định nghĩa __all__ (tùy chọn nhưng nên có)
__all__ = [
    "webhook_router",
    "crud_pr_analysis",
    "schemas_pr_analysis",
]