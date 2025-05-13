# novaguard-backend/app/project_service/__init__.py
from . import crud_project
from . import schemas
from . import api
# Import router từ api.py và đổi tên thành project_router
from .api import router as project_router # <<< DÒNG QUAN TRỌNG

# Cập nhật __all__ để phản ánh tên mới
__all__ = ["crud_project", "schemas", 'api', "project_router"] # <<< KIỂM TRA TÊN Ở ĐÂY