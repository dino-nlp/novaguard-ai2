# novaguard-backend/app/project_service/__init__.py
from . import crud_project
from . import schemas
from . import api
from .api import router  # Re-export router để app.main có thể dùng

__all__ = ["crud_project", "schemas", 'api' ,"router"]