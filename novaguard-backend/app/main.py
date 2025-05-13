from fastapi import FastAPI
from app.core.config import settings # Để có thể dùng settings nếu cần
from app.auth_service import api as auth_api # Import auth_router
from app.project_service import api as project_api
from app.webhook_service import api as webhook_api # Import webhook_router

app = FastAPI(
    title="NovaGuard-AI Backend",
    version="0.1.0", # MVP1 version
    description="API services for NovaGuard-AI platform.",
    # Thêm các thông tin khác nếu muốn
)

# Include a_auth_router
app.include_router(auth_api.router, prefix="/auth", tags=["Authentication"])
app.include_router(project_api.router, prefix="/projects", tags=["Projects"])
app.include_router(webhook_api.router, prefix="/webhooks", tags=["Webhooks"]) 

@app.get("/", tags=["Root"])
async def read_root():
    return {"message": "Welcome to NovaGuard-AI API!"}

# --- Placeholder cho các router khác sẽ thêm sau ---
# from app.project_service import api as project_api
# app.include_router(project_api.router, prefix="/projects", tags=["Projects"])

# from app.webhook_service import api as webhook_api
# app.include_router(webhook_api.router, prefix="/webhooks", tags=["Webhooks"])

if __name__ == "__main__":
    import uvicorn
    # Chỉ dùng cho debug local, không dùng cho production với Docker
    # Dockerfile sẽ dùng uvicorn để chạy app
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")