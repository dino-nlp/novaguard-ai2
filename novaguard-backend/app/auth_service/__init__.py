# novaguard-backend/app/auth_service/__init__.py

# Import các thành phần cần thiết từ các module trong service này
from .api import router as auth_router # Import router từ api.py và đổi tên thành auth_router
from . import crud_user
from . import schemas
from . import auth_bearer # Có thể thêm các module khác nếu cần export

# Định nghĩa __all__ để chỉ định những gì được export khi dùng "from app.auth_service import *"
# Quan trọng hơn, việc import ở trên đã đủ để main.py import auth_router
__all__ = [
    "auth_router",
    "crud_user",
    "schemas",
    "auth_bearer",
]