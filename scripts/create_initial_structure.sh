#!/bin/bash

# Tạo thư mục gốc nếu chưa có
mkdir -p novaguard-ui
mkdir -p novaguard-backend/app/core
mkdir -p novaguard-backend/app/common
mkdir -p novaguard-backend/app/auth_service
mkdir -p novaguard-backend/app/project_service
mkdir -p novaguard-backend/app/webhook_service
mkdir -p novaguard-backend/app/analysis_worker
mkdir -p novaguard-backend/app/models
mkdir -p novaguard-backend/database
mkdir -p novaguard-backend/tests
# mkdir -p novaguard-backend/alembic # Bỏ comment nếu dùng Alembic

# Tạo các file __init__.py để Python nhận diện là package
touch novaguard-backend/app/__init__.py
touch novaguard-backend/app/core/__init__.py
touch novaguard-backend/app/common/__init__.py
touch novaguard-backend/app/auth_service/__init__.py
touch novaguard-backend/app/project_service/__init__.py
touch novaguard-backend/app/webhook_service/__init__.py
touch novaguard-backend/app/analysis_worker/__init__.py
touch novaguard-backend/app/models/__init__.py
touch novaguard-backend/database/__init__.py
touch novaguard-backend/tests/__init__.py
# touch novaguard-backend/alembic/env.py # Bỏ comment nếu dùng Alembic

# Tạo các file cơ bản
touch novaguard-backend/app/main.py
touch novaguard-backend/app/core/config.py
# touch novaguard-backend/alembic.ini # Bỏ comment nếu dùng Alembic
touch novaguard-backend/requirements.txt # Hoặc bạn có thể dùng Poetry/PDM init sau
touch novaguard-backend/Dockerfile

# Tạo thư mục scripts nếu chưa có và file script này
mkdir -p scripts
