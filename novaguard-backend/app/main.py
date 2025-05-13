import logging
from pathlib import Path
from typing import Optional, List, Dict, Any # Thêm Dict, List
import httpx # Thêm httpx
from urllib.parse import urlencode # Thêm urlencode
import secrets # Thêm secrets
from datetime import datetime # Thêm datetime

from fastapi import FastAPI, Request, Depends, Form, HTTPException, status, APIRouter, Query
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session

from app.core.config import settings
# Import trực tiếp router và các thành phần cần thiết từ các service
from app.auth_service.api import router as auth_api_router # API router
from app.auth_service import crud_user as auth_crud # CRUD cho user
from app.auth_service import schemas as auth_schemas # Schemas cho auth
from app.core.security import verify_password, decrypt_data # Security functions

from app.project_service.api import router as project_api_router # API router
from app.project_service import crud_project as project_crud # CRUD cho project
from app.project_service import schemas as project_schemas # Schemas cho project
from app.project_service.api import get_github_repos_for_user_logic 


from app.webhook_service.api import router as webhook_api_router # API router

from app.core.db import get_db
from app.models import User, Project, PRAnalysisRequest, AnalysisFinding
from sqlalchemy import func # Để dùng func.count


# --- Cấu hình Logging cơ bản ---
logger = logging.getLogger("main_app")
if not logger.handlers: # Tránh thêm handler nhiều lần
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(levelname)s [%(name)s:%(lineno)s] - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# --- Xác định đường dẫn cơ sở ---
APP_DIR = Path(__file__).resolve().parent
BASE_DIR = APP_DIR.parent

app = FastAPI(
    title="NovaGuard-AI",
    version="0.1.0",
    description="Intelligent and In-depth Code Analysis Platform.",
)

# --- Session Middleware ---
SESSION_SECRET_KEY = settings.SESSION_SECRET_KEY
if not SESSION_SECRET_KEY or SESSION_SECRET_KEY == "default_session_secret_for_dev_only": # Kiểm tra giá trị mặc định yếu
    logger.warning("SESSION_SECRET_KEY is not securely set in .env. Using a default or potentially insecure key. THIS IS NOT SAFE FOR PRODUCTION.")
    if not SESSION_SECRET_KEY: # Nếu là None, đặt giá trị mặc định yếu để app chạy được
        SESSION_SECRET_KEY = "a_very_default_and_insecure_session_key_for_dev_only_please_change"

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET_KEY,
    session_cookie="novaguard_session",
    max_age=14 * 24 * 60 * 60,  # 14 days
    https_only=False # Đặt True cho production nếu dùng HTTPS
)

# --- Cấu hình Templates (Jinja2) ---
templates_directory = APP_DIR / "templates"
if not templates_directory.is_dir():
    logger.warning(f"Templates directory NOT FOUND at: {templates_directory}. Attempting to create it.")
    try:
        templates_directory.mkdir(parents=True, exist_ok=True)
        logger.info(f"Successfully created templates directory at: {templates_directory}")
    except Exception as e:
        logger.error(f"Could not create templates directory at {templates_directory}: {e}")
        # Có thể raise lỗi ở đây nếu templates là bắt buộc
else:
    logger.info(f"Templates directory configured at: {templates_directory}")
templates = Jinja2Templates(directory=str(templates_directory))


# --- Cấu hình Static Files ---
static_directory = APP_DIR / "static"
if not static_directory.is_dir():
    logger.warning(f"Static files directory NOT FOUND at: {static_directory}. Attempting to create it.")
    try:
        static_directory.mkdir(parents=True, exist_ok=True)
        (static_directory / "css").mkdir(exist_ok=True) # Tạo thư mục con nếu cần
        logger.info(f"Successfully created static files directory at: {static_directory}")
    except Exception as e:
        logger.error(f"Could not create static files directory at {static_directory}: {e}")
else:
    logger.info(f"Static files directory configured at: {static_directory} and will be mounted on /static")
app.mount("/static", StaticFiles(directory=str(static_directory)), name="static")


# --- Include API Routers (cho JSON APIs) ---
app.include_router(auth_api_router, prefix="/api/auth", tags=["API - Authentication"])
app.include_router(project_api_router, prefix="/api/projects", tags=["API - Projects"])
app.include_router(webhook_api_router, prefix="/api/webhooks", tags=["API - Webhooks"])


# --- Helper function to get current user from session for UI ---
async def get_current_ui_user(request: Request, db: Session = Depends(get_db)) -> Optional[auth_schemas.UserPublic]:
    user_id = request.session.get("user_id")
    if user_id:
        try:
            user_db = auth_crud.get_user_by_id(db, user_id=int(user_id))
            if user_db:
                return auth_schemas.UserPublic.model_validate(user_db)
        except ValueError: # Nếu user_id trong session không phải là số
            logger.warning(f"Invalid user_id format in session: {user_id}")
            request.session.pop("user_id", None) # Xóa session hỏng
            request.session.pop("user_email", None)
        except Exception as e:
            logger.exception(f"Error fetching user by ID from session: {e}")
    return None

# --- Web UI Routes ---
# Sử dụng APIRouter cho các nhóm UI để có tổ chức hơn
ui_pages_router = APIRouter(tags=["Web UI - Pages"])
ui_auth_router = APIRouter(prefix="/ui/auth", tags=["Web UI - Authentication"]) # Router cho UI Auth
ui_project_router = APIRouter(prefix="/ui/projects", tags=["Web UI - Projects"]) # Router cho UI Project
ui_report_router = APIRouter(prefix="/ui/reports", tags=["Web UI - Reports"]) # Router cho reports (đã thêm ở kế hoạch trước)


@ui_pages_router.get("/", response_class=HTMLResponse, name="ui_home")
async def serve_home_page(request: Request, current_user: Optional[auth_schemas.UserPublic] = Depends(get_current_ui_user)):
    logger.info(f"Serving home page UI. User: {current_user.email if current_user else 'Guest'}")
    return templates.TemplateResponse("pages/home.html", {
        "request": request,
        "page_title": "Welcome to NovaGuard-AI",
        "current_user": current_user,
        "current_year": datetime.now().year
    })

@ui_pages_router.get("/dashboard", response_class=HTMLResponse, name="ui_dashboard_get")
async def serve_dashboard_page_ui(request: Request, db: Session = Depends(get_db), current_user: Optional[auth_schemas.UserPublic] = Depends(get_current_ui_user)):
    if not current_user:
        flash_messages = request.session.get("_flash_messages", [])
        flash_messages.append({"category": "warning", "message": "Please login to access the dashboard."})
        request.session["_flash_messages"] = flash_messages
        return RedirectResponse(url=request.url_for("ui_login_get"), status_code=status.HTTP_302_FOUND)

    logger.info(f"Serving dashboard page for user: {current_user.email}")
    user_projects = project_crud.get_projects_by_user(db, user_id=current_user.id) # Sử dụng project_crud đã import

    github_connected = False
    available_github_repos: List[project_schemas.GitHubRepoSchema] = []
    db_user_full = db.query(User).filter(User.id == current_user.id).first()
    
    if db_user_full and db_user_full.github_access_token_encrypted:
        github_connected = True
        logger.info(f"User {current_user.email} is connected to GitHub. Fetching their repositories.")
        try:
            # Gọi hàm helper đã refactor từ project_service.api
            # Hàm get_github_repos_for_user_logic đã xử lý việc decrypt token và gọi GitHub API
            github_repos_list_from_api = await get_github_repos_for_user_logic(db_user_full, db)
            
            # Lọc ra những repo chưa được thêm vào NovaGuard
            added_repo_gh_ids = {str(p.github_repo_id) for p in user_projects} # Chuyển sang string để so sánh an toàn
            
            if github_repos_list_from_api:
                for repo_from_gh in github_repos_list_from_api:
                    if str(repo_from_gh.id) not in added_repo_gh_ids: # So sánh ID repo từ GitHub (là số) với github_repo_id (là string trong model)
                        available_github_repos.append(repo_from_gh)
            logger.info(f"Found {len(available_github_repos)} available GitHub repos to add for user {current_user.email}.")

        except Exception as e:
            logger.exception(f"Dashboard: Failed to fetch or filter GitHub repositories for user {current_user.email}: {e}")
            flash_messages = request.session.get("_flash_messages", [])
            flash_messages.append({"category": "error", "message": f"Could not load your GitHub repositories: An error occurred."})
            request.session["_flash_messages"] = flash_messages
            # Vẫn tiếp tục render dashboard với available_github_repos là rỗng
    
    if available_github_repos: # Thêm kiểm tra này
        logger.debug("--- Python Log: AVAILABLE GITHUB REPOS for ui_add_project_get links ---")
        for r_debug in available_github_repos: # Dùng biến khác để tránh nhầm lẫn
            logger.debug(
                f"Repo from Python: {r_debug.full_name}, ID: {r_debug.id} (type: {type(r_debug.id)}), "
                f"Default Branch: {r_debug.default_branch} (type: {type(r_debug.default_branch)})"
            )
        logger.debug("--- Python Log: End of AVAILABLE GITHUB REPOS ---")
    else:
        logger.debug("--- Python Log: No available_github_repos to log details for. ---")
        
    return templates.TemplateResponse("pages/dashboard/dashboard.html", {
        "request": request,
        "page_title": "Dashboard",
        "current_user": current_user,
        "projects": user_projects, # Danh sách project đã thêm vào NovaGuard
        "github_connected": github_connected,
        "available_github_repos": available_github_repos, # Danh sách repo GitHub chưa thêm
        "current_year": datetime.now().year
    })

# --- Auth UI Routes ---
@ui_auth_router.get("/login", response_class=HTMLResponse, name="ui_login_get")
async def login_page_get(request: Request, error: Optional[str] = None, success: Optional[str] = None):
    return templates.TemplateResponse("pages/auth/login.html", {
        "request": request, "page_title": "Login", "error": error, "success": success, "current_year": datetime.now().year
    })

@ui_auth_router.post("/login", response_class=RedirectResponse, name="ui_login_post")
async def login_page_post(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    logger.info(f"UI Login attempt for: {email}")
    user = auth_crud.get_user_by_email(db, email=email)
    if not user or not verify_password(password, user.password_hash): # verify_password đã được import
        logger.warning(f"UI Login failed for {email}: Incorrect email or password.")
        flash_messages = request.session.get("_flash_messages", [])
        flash_messages.append({"category": "error", "message": "Invalid email or password."})
        request.session["_flash_messages"] = flash_messages
        return RedirectResponse(url=request.url_for("ui_login_get"), status_code=status.HTTP_302_FOUND)
    
    request.session["user_id"] = user.id
    request.session["user_email"] = user.email
    logger.info(f"User {user.email} logged in successfully via UI.")
    return RedirectResponse(url=request.url_for("ui_dashboard_get"), status_code=status.HTTP_302_FOUND)

@ui_auth_router.get("/register", response_class=HTMLResponse, name="ui_register_get")
async def register_page_get(request: Request, error: Optional[str] = None):
    return templates.TemplateResponse("pages/auth/register.html", {
        "request": request, "page_title": "Register", "error": error, "current_year": datetime.now().year
    })

@ui_auth_router.post("/register", response_class=RedirectResponse, name="ui_register_post")
async def register_page_post(request: Request, email: str = Form(...), password: str = Form(...), confirm_password: str = Form(...), db: Session = Depends(get_db)):
    logger.info(f"UI Registration attempt for: {email}")
    flash_messages = request.session.get("_flash_messages", [])
    if password != confirm_password:
        flash_messages.append({"category": "error", "message": "Passwords do not match."})
        request.session["_flash_messages"] = flash_messages
        return RedirectResponse(url=request.url_for("ui_register_get"), status_code=status.HTTP_302_FOUND)
    
    db_user = auth_crud.get_user_by_email(db, email=email)
    if db_user:
        flash_messages.append({"category": "error", "message": "Email already registered."})
        request.session["_flash_messages"] = flash_messages
        return RedirectResponse(url=request.url_for("ui_register_get"), status_code=status.HTTP_302_FOUND)
    
    try:
        user_in_create = auth_schemas.UserCreate(email=email, password=password)
        auth_crud.create_user(db=db, user=user_in_create) # is_oauth_user=False là mặc định
        logger.info(f"User {email} registered successfully via UI.")
        flash_messages.append({"category": "success", "message": "Registration successful! Please login."})
        request.session["_flash_messages"] = flash_messages
        return RedirectResponse(url=request.url_for("ui_login_get"), status_code=status.HTTP_302_FOUND)
    except Exception as e:
        logger.exception(f"Error during UI registration for {email}")
        flash_messages.append({"category": "error", "message": "An unexpected error occurred during registration."})
        request.session["_flash_messages"] = flash_messages
        return RedirectResponse(url=request.url_for("ui_register_get"), status_code=status.HTTP_302_FOUND)

@ui_auth_router.get("/logout", response_class=RedirectResponse, name="ui_logout_get")
async def logout_page_get(request: Request):
    user_email = request.session.pop("user_email", "Unknown user")
    request.session.pop("user_id", None)
    logger.info(f"User {user_email} logged out from UI.")
    flash_messages = request.session.get("_flash_messages", [])
    flash_messages.append({"category": "info", "message": "You have been logged out."})
    request.session["_flash_messages"] = flash_messages
    return RedirectResponse(url=request.url_for("ui_home"), status_code=status.HTTP_302_FOUND)
        
# --- Project UI Routes ---
@ui_project_router.get("/add", response_class=HTMLResponse, name="ui_add_project_get")
async def add_project_page_ui_get(
    request: Request, # << LUÔN ĐẶT REQUEST LÊN ĐẦU (hoặc sau path params nếu có)
    # Các Query parameters, tên phải khớp với key trong url_for
    gh_repo_id: Optional[str] = Query(None), # Bỏ alias, FastAPI sẽ dùng 'gh_repo_id' làm tên query param
    gh_repo_name: Optional[str] = Query(None), # Bỏ alias
    gh_main_branch: Optional[str] = Query(None), # Bỏ alias
    # Các Dependencies ở cuối
    db: Session = Depends(get_db),
    current_user: Optional[auth_schemas.UserPublic] = Depends(get_current_ui_user)
):
    logger.info(
        f"Serving 'Add Project' page for user: {current_user.email if current_user else 'Guest'}. "
        f"Prefill data from query: gh_repo_id={gh_repo_id}, gh_repo_name={gh_repo_name}, gh_main_branch={gh_main_branch}"
    )

    error_message_github: Optional[str] = None
    github_connected = False
    if current_user:
        db_user_full = db.query(User).filter(User.id == current_user.id).first()
        if db_user_full and db_user_full.github_access_token_encrypted:
            github_connected = True
        else:
            error_message_github = "GitHub account not connected. If you add a project manually, webhooks might not be created automatically. Please connect your GitHub account via the Dashboard."
    else:
        error_message_github = "Please login to connect your GitHub account."
        # Nếu không có current_user, có thể redirect về login luôn ở đây
        # flash_messages = request.session.get("_flash_messages", [])
        # flash_messages.append({"category": "warning", "message": "Please login to add a project."})
        # request.session["_flash_messages"] = flash_messages
        # return RedirectResponse(url=request.url_for("ui_login_get"), status_code=status.HTTP_302_FOUND)
        # Tuy nhiên, get_current_ui_user đã là Optional, nên có thể để template xử lý việc current_user là None

    prefill_data = {
        "repo_id": gh_repo_id,
        "repo_name": gh_repo_name,
        "main_branch": gh_main_branch if gh_main_branch else "main"
    }

    return templates.TemplateResponse("pages/projects/add_project.html", {
        "request": request,
        "page_title": "Add New Project",
        "current_user": current_user,
        "github_connected": github_connected, 
        "error_github": error_message_github,
        "prefill_data": prefill_data,
        "current_year": datetime.now().year
    })

@ui_project_router.post("/add", response_class=RedirectResponse, name="ui_add_project_post")
async def add_project_page_ui_post( # Đổi tên hàm
    request: Request,
    db: Session = Depends(get_db),
    current_user: auth_schemas.UserPublic = Depends(get_current_ui_user),
    github_repo_id: str = Form(...), 
    repo_name: str = Form(...), # owner/repo
    main_branch: str = Form(...),
    language: Optional[str] = Form(None),
    custom_project_notes: Optional[str] = Form(None)
):
    if not current_user: # Should be caught by dependency if endpoint is protected
        return RedirectResponse(url=request.url_for("ui_login_get"))

    logger.info(f"UI: User {current_user.email} submitting new project: Repo Name '{repo_name}', GitHub Repo ID '{github_repo_id}'")
    flash_messages = request.session.get("_flash_messages", [])

    # Logic tạo project và webhook (tạm thời lặp lại, cần refactor)
    project_create_schema = project_schemas.ProjectCreate(
        github_repo_id=github_repo_id, repo_name=repo_name, main_branch=main_branch,
        language=language, custom_project_notes=custom_project_notes
    )
    db_project = project_crud.create_project(db=db, project_in=project_create_schema, user_id=current_user.id)

    if not db_project:
        flash_messages.append({"category": "error", "message": "Failed to add project. It might already exist or there was a database issue."})
        request.session["_flash_messages"] = flash_messages
        return RedirectResponse(url=request.url_for("ui_add_project_get"), status_code=status.HTTP_302_FOUND)
    
    logger.info(f"UI: Project '{db_project.repo_name}' (ID: {db_project.id}) created in DB for user {current_user.email}.")

    db_user_full = db.query(User).filter(User.id == current_user.id).first()
    if not db_user_full or not db_user_full.github_access_token_encrypted:
        # Project đã tạo, nhưng không thể tạo webhook. Thông báo cho người dùng.
        flash_messages.append({"category": "warning", "message": f"Project '{db_project.repo_name}' added, but GitHub token is missing. Please connect GitHub via Dashboard and then try to set up webhook manually or re-add project."})
        request.session["_flash_messages"] = flash_messages
        return RedirectResponse(url=request.url_for("ui_dashboard_get"), status_code=status.HTTP_302_FOUND)

    github_token = decrypt_data(db_user_full.github_access_token_encrypted)
    if not github_token:
        flash_messages.append({"category": "error", "message": f"Project '{db_project.repo_name}' added, but GitHub token decryption failed. Webhook not created."})
        request.session["_flash_messages"] = flash_messages
        return RedirectResponse(url=request.url_for("ui_dashboard_get"), status_code=status.HTTP_302_FOUND)

    if not settings.NOVAGUARD_PUBLIC_URL:
        flash_messages.append({"category": "warning", "message": f"Project '{db_project.repo_name}' added, but server's public URL for webhooks is not configured. Webhook setup skipped."})
        request.session["_flash_messages"] = flash_messages
        return RedirectResponse(url=request.url_for("ui_dashboard_get"), status_code=status.HTTP_302_FOUND)

    webhook_payload_url = f"{settings.NOVAGUARD_PUBLIC_URL.rstrip('/')}/api/webhooks/github" # Webhook API endpoint
    github_hook_data = {
        "name": "web", "active": True, "events": ["pull_request"],
        "config": {"url": webhook_payload_url, "content_type": "json", "secret": settings.GITHUB_WEBHOOK_SECRET}
    }
    create_hook_url = f"https://api.github.com/repos/{db_project.repo_name}/hooks"
    headers_gh = {"Authorization": f"token {github_token}", "Accept": "application/vnd.github.v3+json", "X-GitHub-Api-Version": "2022-11-28"}
    
    webhook_id_on_github: Optional[str] = None
    webhook_creation_error: Optional[str] = None
    try:
        async with httpx.AsyncClient() as client:
            hook_response = await client.post(create_hook_url, json=github_hook_data, headers=headers_gh)
            if hook_response.status_code == 201:
                webhook_id_on_github = str(hook_response.json().get("id"))
            elif hook_response.status_code == 422 and "Hook already exists" in hook_response.text:
                webhook_creation_error = "Webhook already exists on this repository."
            else:
                hook_response.raise_for_status()
    except httpx.HTTPStatusError as e:
        webhook_creation_error = f"GitHub API error: {e.response.status_code} - {e.response.json().get('message', str(e))}"
    except Exception as e:
        webhook_creation_error = f"Unexpected error creating webhook: {str(e)}"

    if webhook_id_on_github:
        db_project.github_webhook_id = webhook_id_on_github
        db.commit(); db.refresh(db_project)
        flash_messages.append({"category": "success", "message": f"Project '{db_project.repo_name}' added and webhook (ID: {webhook_id_on_github}) created successfully!"})
    elif webhook_creation_error:
        flash_messages.append({"category": "warning", "message": f"Project '{db_project.repo_name}' added, but webhook creation failed: {webhook_creation_error}"})
    else:
        flash_messages.append({"category": "info", "message": f"Project '{db_project.repo_name}' added. Webhook was not created (no specific error, or already exists without ID retrieval)."})
    
    request.session["_flash_messages"] = flash_messages
    return RedirectResponse(url=request.url_for("ui_dashboard_get"), status_code=status.HTTP_302_FOUND)

# Include UI Routers
app.include_router(ui_pages_router)
app.include_router(ui_auth_router) # Đã có prefix /ui/auth
app.include_router(ui_project_router) # Đã có prefix /ui/projects
app.include_router(ui_report_router) # Router cho reports


# --- In thông tin các route đã đăng ký để debug ---
if settings.DEBUG: # Chỉ in ra khi ở chế độ DEBUG (thêm DEBUG=True vào .env nếu cần)
    logger.info("="*50)
    logger.info("REGISTERED ROUTES:")
    for route in app.routes:
        if hasattr(route, "name"): # Các APIRoute sẽ có thuộc tính name
            logger.info(f"Name: {route.name}, Path: {route.path}, Methods: {getattr(route, 'methods', None)}")
            if route.name == "ui_add_project_get":
                logger.info(f"  Specifics for 'ui_add_project_get':")
                logger.info(f"    Class: {type(route)}")
                # Cố gắng xem các tham số mà route này mong đợi (có thể không dễ lấy trực tiếp)
                # Tuy nhiên, path ở trên đã cho biết nó có path params hay không.
                # Nếu route.path_format là /ui/projects/add thì không có path params.
                logger.info(f"    Path Format: {getattr(route, 'path_format', route.path)}")


    logger.info("="*50)
# --- Kết thúc phần in thông tin route ---

# --- Khởi chạy Uvicorn ---
if __name__ == "__main__":
    import uvicorn
    log_config = uvicorn.config.LOGGING_CONFIG
    log_config["formatters"]["access"]["fmt"] = '%(asctime)s %(levelname)s [%(name)s:%(lineno)d] [%(client_addr)s] - "%(request_line)s" %(status_code)s'
    log_config["formatters"]["default"]["fmt"] = "%(asctime)s %(levelname)s [%(name)s:%(lineno)d] - %(message)s"
    
    logger.info("Attempting to start Uvicorn server directly from main.py...")
    print(f"Templates directory: {templates_directory}")
    print(f"Static files directory: {static_directory}")

    uvicorn.run(
        "main:app", host="0.0.0.0", port=8000, 
        log_level="debug", reload=True, log_config=log_config
    )