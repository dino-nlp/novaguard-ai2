import logging
from pathlib import Path
from typing import Optional, List, Dict, Any, Union
import httpx
from urllib.parse import urlencode
import secrets
from datetime import datetime, timezone

from fastapi import FastAPI, Request, Depends, Form, HTTPException, status, APIRouter, Query
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.config import settings
from app.auth_service.api import router as auth_api_router
from app.auth_service import crud_user as auth_crud
from app.auth_service import schemas as auth_schemas
from app.core.security import verify_password, decrypt_data

from app.project_service.api import router as project_api_router
from app.project_service.api import get_github_repos_for_user_logic # << IMPORT HÀM HELPER
from app.project_service import crud_project as project_crud
from app.project_service import schemas as project_schemas

from app.webhook_service.api import router as webhook_api_router
from app.webhook_service import crud_pr_analysis as pr_crud # Đổi tên để tránh xung đột với project_crud
from app.webhook_service import schemas_pr_analysis # Không đổi tên schema để dễ theo dõi
from app.project_service import crud_full_scan # Import crud cho full scan
from app.project_service.schemas import AnalysisHistoryItem # Import schema mới

from app.analysis_module import crud_finding as finding_crud
from app.analysis_module import schemas_finding as finding_schemas

from app.core.db import get_db
from app.models import User, Project, PRAnalysisRequest, AnalysisFinding, FullProjectAnalysisRequest, PyAnalysisSeverity
from app.models.pr_analysis_request_model import PRAnalysisStatus
from app.models.full_project_analysis_request_model import FullProjectAnalysisStatus
from app.models.analysis_finding_model import PyFindingLevel # Nếu bạn dùng trực tiếp Enum này để filter
from app.core.graph_db import close_async_neo4j_driver, get_async_neo4j_driver # Import các hàm Neo4j
from app.common.message_queue.kafka_producer import send_pr_analysis_task # Đảm bảo import này



logger = logging.getLogger("main_app")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(levelname)s [%(name)s:%(lineno)s] - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if settings.DEBUG else logging.INFO)


APP_DIR = Path(__file__).resolve().parent
BASE_DIR = APP_DIR.parent

app = FastAPI(
    title="NovaGuard-AI",
    version="0.1.0",
    description="Intelligent and In-depth Code Analysis Platform.",
    debug=settings.DEBUG
)

SESSION_SECRET_KEY = settings.SESSION_SECRET_KEY
if not SESSION_SECRET_KEY or SESSION_SECRET_KEY == "default_session_secret_for_dev_only":
    logger.warning("SESSION_SECRET_KEY is not securely set in .env.")
    if not SESSION_SECRET_KEY:
        SESSION_SECRET_KEY = "a_very_default_and_insecure_session_key_for_dev_only_please_change"

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET_KEY,
    session_cookie="novaguard_session",
    max_age=14 * 24 * 60 * 60,
    https_only=False
)

templates_directory = APP_DIR / "templates"
if not templates_directory.is_dir():
    logger.error(f"Templates directory NOT FOUND at: {templates_directory}")
else:
    logger.info(f"Templates directory configured at: {templates_directory}")
templates = Jinja2Templates(directory=str(templates_directory))

static_directory = APP_DIR / "static"
if not static_directory.is_dir():
    logger.error(f"Static files directory NOT FOUND at: {static_directory}")
else:
    logger.info(f"Static files directory configured at: {static_directory} and will be mounted on /static")
app.mount("/static", StaticFiles(directory=str(static_directory)), name="static")

app.include_router(auth_api_router, prefix="/api/auth", tags=["API - Authentication"])
app.include_router(project_api_router, prefix="/api/projects", tags=["API - Projects"])
app.include_router(webhook_api_router, prefix="/api/webhooks", tags=["API - Webhooks"])


@app.on_event("startup")
async def on_startup_event():
    logger.info("Application is starting up...")
    # Kiểm tra kết nối Neo4j khi khởi động (tùy chọn nhưng tốt)
    try:
        driver = await get_async_neo4j_driver()
        if driver:
            await driver.verify_connectivity()
            logger.info("Successfully connected to Neo4j and verified connectivity.")
        else:
            logger.error("Neo4j driver could not be initialized on startup.")
    except Exception as e:
        logger.error(f"Failed to connect to Neo4j on startup: {e}", exc_info=True)
        # Bạn có thể quyết định có nên dừng ứng dụng ở đây không nếu Neo4j là critical
        # raise RuntimeError("Failed to connect to Neo4j, application cannot start.") from e

@app.on_event("shutdown")
async def on_shutdown_event():
    logger.info("Application is shutting down...")
    await close_async_neo4j_driver() # Đóng driver Neo4j
    # Thêm các cleanup khác nếu có
    logger.info("Application shutdown complete.")

async def get_current_ui_user(request: Request, db: Session = Depends(get_db)) -> Optional[auth_schemas.UserPublic]:
    user_id = request.session.get("user_id")
    if user_id:
        try:
            user_db = auth_crud.get_user_by_id(db, user_id=int(user_id))
            if user_db:
                return auth_schemas.UserPublic.model_validate(user_db)
        except ValueError:
            logger.warning(f"Invalid user_id format in session: {user_id}")
            request.session.pop("user_id", None)
            request.session.pop("user_email", None)
        except Exception as e:
            logger.exception(f"Error fetching user by ID from session: {e}")
    return None

ui_pages_router = APIRouter(tags=["Web UI - Pages"])
ui_auth_router = APIRouter(prefix="/ui/auth", tags=["Web UI - Authentication"])
ui_project_router = APIRouter(prefix="/ui/projects", tags=["Web UI - Projects"])
ui_report_router = APIRouter(prefix="/ui/reports", tags=["Web UI - Reports"])

@ui_pages_router.get("/", response_class=HTMLResponse, name="ui_home")
async def serve_home_page(request: Request, current_user: Optional[auth_schemas.UserPublic] = Depends(get_current_ui_user)):
    logger.debug(f"HOME - Path: {request.url.path}, Query Params: {request.query_params}")
    logger.debug(f"HOME - Session: {request.session}")
    logger.info(f"Serving home page UI. User: {current_user.email if current_user else 'Guest'}")
    return templates.TemplateResponse("pages/home.html", {
        "request": request,
        "page_title": "Welcome to NovaGuard-AI",
        "current_user": current_user,
        "current_year": datetime.now().year
    })

@ui_pages_router.get("/dashboard", response_class=HTMLResponse, name="ui_dashboard_get")
async def serve_dashboard_page_ui(
    request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[auth_schemas.UserPublic] = Depends(get_current_ui_user)
):
    logger.debug(f"DASHBOARD - Path: {request.url.path}, Query Params: {request.query_params}")
    logger.debug(f"DASHBOARD - Session: {request.session}")

    if not current_user:
        flash_messages = request.session.get("_flash_messages", [])
        flash_messages.append({"category": "warning", "message": "Please login to access the dashboard."})
        request.session["_flash_messages"] = flash_messages
        return RedirectResponse(url=request.url_for("ui_login_get"), status_code=status.HTTP_302_FOUND)

    logger.info(f"Serving dashboard page for user: {current_user.email}") # Dòng 164 của bạn
    user_projects = project_crud.get_projects_by_user(db, user_id=current_user.id, limit=1000)

    github_connected = False
    available_github_repos: List[project_schemas.GitHubRepoSchema] = []
    
    db_user_full = db.query(User).filter(User.id == current_user.id).first()

    if db_user_full and db_user_full.github_access_token_encrypted:
        github_connected = True
        logger.info(f"User {current_user.email} is connected to GitHub. Fetching their repositories.") # Dòng 174
        try:
            github_repos_list_from_api = await get_github_repos_for_user_logic(db_user_full, db)
            
            added_repo_gh_ids = {str(p.github_repo_id) for p in user_projects}
            
            if github_repos_list_from_api:
                for repo_from_gh in github_repos_list_from_api:
                    if str(repo_from_gh.id) not in added_repo_gh_ids:
                        available_github_repos.append(repo_from_gh)
            
            if available_github_repos:
                logger.debug("--- Python Log: AVAILABLE GITHUB REPOS for ui_add_project_get links ---") # Dòng 186
                for r_debug in available_github_repos:
                    logger.debug( # Dòng 188
                        f"Repo from Python: {r_debug.full_name}, ID: {r_debug.id} (type: {type(r_debug.id)}), "
                        f"Default Branch: {r_debug.default_branch} (type: {type(r_debug.default_branch)})"
                    )
                logger.debug("--- Python Log: End of AVAILABLE GITHUB REPOS ---") # Dòng 192
            else:
                logger.debug("--- Python Log: No available_github_repos to log details for. ---")
            logger.info(f"Found {len(available_github_repos)} available GitHub repos to add for user {current_user.email}.") # Dòng 195

        except Exception as e:
            logger.exception(f"Dashboard: Failed to fetch or filter GitHub repositories for user {current_user.email}: {e}")
            flash_messages = request.session.get("_flash_messages", [])
            flash_messages.append({"category": "error", "message": f"Could not load your GitHub repositories: An error occurred."})
            request.session["_flash_messages"] = flash_messages
            
    return templates.TemplateResponse("pages/dashboard/dashboard.html", { # Dòng 203 của bạn
        "request": request,
        "page_title": "Dashboard",
        "current_user": current_user,
        "projects": user_projects,
        "github_connected": github_connected,
        "available_github_repos": available_github_repos,
        "current_year": datetime.now().year
    })

@ui_auth_router.get("/login", response_class=HTMLResponse, name="ui_login_get")
async def login_page_get(request: Request, error: Optional[str] = None, success: Optional[str] = None):
    return templates.TemplateResponse("pages/auth/login.html", {
        "request": request, "page_title": "Login", "error": error, "success": success, "current_year": datetime.now().year
    })

@ui_auth_router.post("/login", response_class=RedirectResponse, name="ui_login_post")
async def login_page_post(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    logger.info(f"UI Login attempt for: {email}")
    user = auth_crud.get_user_by_email(db, email=email)
    if not user or not verify_password(password, user.password_hash):
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
        auth_crud.create_user(db=db, user=user_in_create)
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
        
@ui_project_router.get("/add", response_class=HTMLResponse, name="ui_add_project_get")
async def add_project_page_ui_get(
    request: Request,                                      # 1.
    gh_repo_id: Optional[str] = Query(None),                # 2. Query param (NO ALIAS)
    gh_repo_name: Optional[str] = Query(None),              # 3. Query param (NO ALIAS)
    gh_main_branch: Optional[str] = Query(None),            # 4. Query param (NO ALIAS)
    db: Session = Depends(get_db),                          # 5. Dependency
    current_user: Optional[auth_schemas.UserPublic] = Depends(get_current_ui_user) # 6. Dependency
):
    logger.debug(
        f"HANDLER add_project_page_ui_get - Received query: "
        f"gh_repo_id='{gh_repo_id}', gh_repo_name='{gh_repo_name}', gh_main_branch='{gh_main_branch}'"
    )
    if not current_user:
        flash_messages = request.session.get("_flash_messages", [])
        flash_messages.append({"category": "warning", "message": "Please login to add a project."})
        request.session["_flash_messages"] = flash_messages
        return RedirectResponse(url=request.url_for("ui_login_get"), status_code=status.HTTP_302_FOUND)

    error_message_github: Optional[str] = None
    github_connected = False
    db_user_full = db.query(User).filter(User.id == current_user.id).first() 
    if db_user_full and db_user_full.github_access_token_encrypted:
        github_connected = True
    else:
        error_message_github = "GitHub account not connected. If you add a project manually, webhooks might not be created automatically. Please connect your GitHub account via the Dashboard."
    
    prefill_data = {
        "repo_id": gh_repo_id,
        "repo_name": gh_repo_name,
        "main_branch": gh_main_branch if gh_main_branch else "main"
    }
    
    return templates.TemplateResponse("pages/projects/add_project.html", {
        "request": request, "page_title": "Add New Project", "current_user": current_user,
        "github_connected": github_connected,
        "error_github": error_message_github, 
        "prefill_data": prefill_data, 
        "current_year": datetime.now().year
    })

@ui_project_router.post("/add", response_class=RedirectResponse, name="ui_add_project_post")
async def add_project_page_ui_post(
    request: Request, 
    # Các Form fields đứng trước Dependencies
    github_repo_id: str = Form(...), 
    repo_name: str = Form(...),
    main_branch: str = Form(...),
    language: Optional[str] = Form(None),
    custom_project_notes: Optional[str] = Form(None),
    # Dependencies
    db: Session = Depends(get_db), 
    current_user: auth_schemas.UserPublic = Depends(get_current_ui_user)
):
    if not current_user: # Redundant if Depends(get_current_ui_user) handles unauthenticated
        return RedirectResponse(url=request.url_for("ui_login_get"), status_code=status.HTTP_302_FOUND)

    logger.info(f"UI: User {current_user.email} submitting new project: Repo Name '{repo_name}', GitHub Repo ID '{github_repo_id}'")
    flash_messages = request.session.get("_flash_messages", [])

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
    webhook_created_successfully = False # Cờ để kiểm tra trạng thái webhook
    if db_user_full and db_user_full.github_access_token_encrypted:
        github_token = decrypt_data(db_user_full.github_access_token_encrypted)
        if github_token and settings.NOVAGUARD_PUBLIC_URL and '/' in db_project.repo_name:
            webhook_payload_url = f"{settings.NOVAGUARD_PUBLIC_URL.rstrip('/')}/api/webhooks/github"
            github_hook_data = {
                "name": "web", "active": True, "events": ["pull_request"],
                "config": {"url": webhook_payload_url, "content_type": "json", "secret": settings.GITHUB_WEBHOOK_SECRET}
            }
            create_hook_url = f"https://api.github.com/repos/{db_project.repo_name}/hooks"
            headers_gh = {"Authorization": f"token {github_token}", "Accept": "application/vnd.github.v3+json", "X-GitHub-Api-Version": "2022-11-28"}
            
            webhook_id_on_github: Optional[str] = None
            try:
                async with httpx.AsyncClient(timeout=10.0) as client: # Thêm timeout
                    hook_response = await client.post(create_hook_url, json=github_hook_data, headers=headers_gh)
                    if hook_response.status_code == 201:
                        webhook_id_on_github = str(hook_response.json().get("id"))
                        db_project.github_webhook_id = webhook_id_on_github
                        db.commit()
                        db.refresh(db_project)
                        webhook_created_successfully = True
                        logger.info(f"Successfully created webhook (ID: {webhook_id_on_github}) for project '{db_project.repo_name}' and saved to DB.")
                    elif hook_response.status_code == 422 and "Hook already exists" in hook_response.text:
                        logger.warning(f"Webhook already exists for '{db_project.repo_name}'. Assuming it's correctly configured.")
                        # Bạn có thể muốn thử lấy ID của hook đã tồn tại và lưu lại ở đây nếu chưa có.
                        webhook_created_successfully = True # Coi như thành công nếu đã tồn tại
                    else:
                        logger.error(f"Failed to create webhook (Status {hook_response.status_code}): {hook_response.text} for {db_project.repo_name}")
                        hook_response.raise_for_status() # Sẽ raise lỗi và đi vào except httpx.HTTPStatusError
            except httpx.HTTPStatusError as e_http_hook:
                error_detail = e_http_hook.response.json().get('message', str(e_http_hook)) if e_http_hook.response.content else str(e_http_hook)
                logger.error(f"HTTP error creating GitHub webhook for '{db_project.repo_name}': {e_http_hook.response.status_code} - {error_detail}")
                flash_messages.append({"category": "warning", "message": f"Project '{db_project.repo_name}' added, but webhook creation failed: {error_detail}"})
            except Exception as e_hook:
                logger.exception(f"Unexpected error creating GitHub webhook for '{db_project.repo_name}'.")
                flash_messages.append({"category": "warning", "message": f"Project '{db_project.repo_name}' added, but webhook creation failed with an unexpected error."})
        # Các trường hợp lỗi token, URL, format tên repo
        elif not github_token:
            flash_messages.append({"category": "error", "message": f"Project '{db_project.repo_name}' added, but GitHub token decryption failed. Webhook not created."})
        elif not settings.NOVAGUARD_PUBLIC_URL:
             flash_messages.append({"category": "warning", "message": f"Project '{db_project.repo_name}' added, but server's public URL for webhooks is not configured. Webhook setup skipped."})
        elif '/' not in db_project.repo_name: # Đã kiểm tra ở project_service.api nhưng để an toàn
            flash_messages.append({"category": "error", "message": f"Project '{db_project.repo_name}' added, but repository name format is invalid ('owner/repo'). Webhook not created."})
    else:
        flash_messages.append({"category": "warning", "message": f"Project '{db_project.repo_name}' added, but GitHub account is not connected or token is missing. Webhook setup skipped. Please connect/reconnect GitHub via Dashboard."})

    if webhook_created_successfully: # Chỉ thêm message success nếu webhook thực sự OK
         flash_messages.append({"category": "success", "message": f"Project '{db_project.repo_name}' added and webhook integration is set up!"})
    elif not any(fm["category"] == "error" or fm["category"] == "warning" for fm in flash_messages): # Nếu không có lỗi/cảnh báo nào về webhook
        flash_messages.append({"category": "success", "message": f"Project '{db_project.repo_name}' added."}) # Message chung
    
    request.session["_flash_messages"] = flash_messages
    return RedirectResponse(url=request.url_for("ui_dashboard_get"), status_code=status.HTTP_302_FOUND)

@ui_project_router.post("/{project_id_path}/trigger-full-scan", name="ui_trigger_full_scan_post")
async def ui_trigger_full_scan_for_project_post(
    request: Request, # Cần request object cho flash messages hoặc session
    project_id_path: int,
    db: Session = Depends(get_db),
    current_user: auth_schemas.UserPublic = Depends(get_current_ui_user) # Sử dụng session auth
):
    if not current_user:
        # JavaScript client sẽ nhận lỗi JSON, không phải redirect HTML trực tiếp từ đây
        # nếu nó là một AJAX request.
        # Tuy nhiên, với POST từ form thuần túy (không JS), redirect sẽ hoạt động.
        # Vì JS đang gọi, chúng ta nên trả về lỗi JSON.
        logger.warning(f"UI Trigger Full Scan: Unauthorized attempt for project ID {project_id_path}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated. Please login.")

    logger.info(f"UI: User {current_user.email} triggering full project scan for project ID: {project_id_path}")
    db_project = project_crud.get_project_by_id(db, project_id=project_id_path, user_id=current_user.id)
    if not db_project:
        logger.warning(f"UI Trigger Full Scan: Project ID {project_id_path} not found or not owned by user {current_user.email}")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found or not owned by user")

    # Kiểm tra xem có scan nào đang chạy không (tái sử dụng logic từ API)
    existing_scans = db.query(FullProjectAnalysisRequest).filter(
        FullProjectAnalysisRequest.project_id == project_id_path,
        FullProjectAnalysisRequest.status.in_([
            FullProjectAnalysisStatus.PENDING, FullProjectAnalysisStatus.PROCESSING,
            FullProjectAnalysisStatus.SOURCE_FETCHED, FullProjectAnalysisStatus.CKG_BUILDING,
            FullProjectAnalysisStatus.ANALYZING
        ])
    ).first()
    if existing_scans:
        logger.warning(f"UI Trigger Full Scan: Scan already in progress for project ID {project_id_path} (Request ID: {existing_scans.id})")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A full project scan is already in progress or pending (ID: {existing_scans.id}, Status: {existing_scans.status.value})."
        )

    try:
        scan_request = crud_full_scan.create_full_scan_request(
            db=db, project_id=db_project.id, branch_name=db_project.main_branch
        )
        logger.info(f"UI Trigger Full Scan: Created FullProjectAnalysisRequest ID: {scan_request.id} for project {db_project.repo_name}")

        kafka_task_data = {
            "task_type": "full_project_scan",
            "full_project_analysis_request_id": scan_request.id,
            "project_id": db_project.id,
            "user_id": current_user.id,
            "github_repo_id": db_project.github_repo_id,
            "repo_full_name": db_project.repo_name,
            "branch_to_scan": db_project.main_branch,
        }
        
        success_kafka = await send_pr_analysis_task(kafka_task_data) # Đảm bảo hàm này là async hoặc chạy trong thread executor nếu blocking
        
        if success_kafka:
            logger.info(f"UI Trigger Full Scan: Task for FullProjectAnalysisRequest ID {scan_request.id} sent to Kafka.")
            # Trả về JSON response cho JavaScript client
            return {
                "message": f"Full scan request (ID: {scan_request.id}) for branch '{db_project.main_branch}' has been successfully queued.",
                "id": scan_request.id,
                "status": scan_request.status.value
            }
        else:
            logger.error(f"UI Trigger Full Scan: Failed to send task for FullProjectAnalysisRequest ID {scan_request.id} to Kafka.")
            crud_full_scan.update_full_scan_request_status(db, scan_request.id, FullProjectAnalysisStatus.FAILED, "Kafka send error")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to queue analysis task due to Kafka issue.")

    except Exception as e:
        logger.exception(f"UI Trigger Full Scan: Error triggering full scan for project ID {project_id_path}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An unexpected error occurred: {str(e)}")


@ui_project_router.get(
    "/list-gh-repos-for-ui", 
    response_model=List[project_schemas.GitHubRepoSchema], # Response là danh sách các repo schema
    name="ui_list_gh_repos_for_form", # Đặt tên route rõ ràng
    summary="Fetch user's GitHub repos for UI forms (uses session auth)"
)
async def ui_list_github_repos_for_add_project_form( # Đổi tên hàm để rõ mục đích
    request: Request, # Cần request object
    db: Session = Depends(get_db),
    current_ui_user: Optional[auth_schemas.UserPublic] = Depends(get_current_ui_user) # Xác thực qua session
):
    if not current_ui_user:
        logger.warning("UI list GH repos: Attempt to list GH repos for UI without active session.")
        # JavaScript sẽ xử lý lỗi này, nên không cần flash message ở đây
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="User not authenticated. Please login again."
        )

    # Lấy đối tượng User đầy đủ từ DB để có token mã hóa
    db_user_full = db.query(User).filter(User.id == current_ui_user.id).first()
    if not db_user_full:
        logger.error(f"UI list GH repos: User ID {current_ui_user.id} from session not found in DB.")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found in database.")

    if not db_user_full.github_access_token_encrypted:
        logger.info(f"UI list GH repos: User {current_ui_user.email} has not connected GitHub account. Returning empty list.")
        return [] # Trả về list rỗng nếu chưa kết nối GitHub

    try:
        # Gọi hàm logic đã được refactor từ project_service/api.py
        repos = await get_github_repos_for_user_logic(db_user_full, db)
        logger.info(f"UI list GH repos: Successfully fetched {len(repos)} repos for {current_ui_user.email}")
        return repos
    except Exception as e:
        logger.exception(f"UI list GH repos: Error calling get_github_repos_for_user_logic for {current_ui_user.email}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="Could not fetch repositories from GitHub due to a server error."
        )

@ui_project_router.get("/{project_id_path}", response_class=HTMLResponse, name="ui_project_detail_get")
async def project_detail_page_ui_get(
    request: Request,
    project_id_path: int,
    db: Session = Depends(get_db),
    current_user: Optional[auth_schemas.UserPublic] = Depends(get_current_ui_user)
):
    if not current_user:
        flash_messages = request.session.get("_flash_messages", [])
        flash_messages.append({"category": "warning", "message": "Please login to view project details."})
        request.session["_flash_messages"] = flash_messages
        return RedirectResponse(url=request.url_for("ui_login_get"), status_code=status.HTTP_302_FOUND)

    logger.info(f"Serving project detail page for project ID: {project_id_path}, user: {current_user.email}")
    project_details = project_crud.get_project_by_id(db, project_id=project_id_path, user_id=current_user.id)
    
    if not project_details:
        # ... (xử lý project không tìm thấy)
        logger.warning(f"Project ID {project_id_path} not found or not owned by user {current_user.email}.")
        flash_messages = request.session.get("_flash_messages", [])
        flash_messages.append({"category": "error", "message": "Project not found or you do not have access."})
        request.session["_flash_messages"] = flash_messages
        return RedirectResponse(url=request.url_for("ui_dashboard_get"), status_code=status.HTTP_302_FOUND)

    pr_scans_db = pr_crud.get_pr_analysis_requests_by_project_id(db, project_id=project_id_path, limit=10)
    full_scans_db = crud_full_scan.get_full_scan_requests_for_project(db, project_id=project_id_path, limit=10)
    analysis_history: List[AnalysisHistoryItem] = []

    # Xử lý PR Scans
    for pr_req_db in pr_scans_db:
        errors, warnings, others = 0, 0, 0
        if pr_req_db.status == PRAnalysisStatus.COMPLETED:
            findings_severities = db.query(AnalysisFinding.severity)\
                                    .filter(AnalysisFinding.pr_analysis_request_id == pr_req_db.id)\
                                    .all()
            for severity_tuple in findings_severities:
                severity_enum_member = severity_tuple[0]
                if severity_enum_member == PyAnalysisSeverity.ERROR: errors += 1
                elif severity_enum_member == PyAnalysisSeverity.WARNING: warnings += 1
                else: others += 1
        
        report_url_str = None
        try:
            report_url_str = str(request.url_for('ui_pr_report_get', request_id_param=pr_req_db.id))
        except Exception as e:
            logger.warning(f"Could not generate report URL for PR scan {pr_req_db.id}: {e}")

        analysis_history.append(
            AnalysisHistoryItem(
                id=pr_req_db.id, scan_type="pr",
                identifier=f"PR #{pr_req_db.pr_number}", title=pr_req_db.pr_title,
                status=pr_req_db.status.value, # Truyền string value của Enum
                requested_at=pr_req_db.requested_at, report_url=report_url_str,
                total_errors=errors, total_warnings=warnings, total_other_findings=others
            )
        )
        
    # Xử lý Full Project Scans
    for full_req_db in full_scans_db:
        errors_full, warnings_full, others_full = 0, 0, 0
        if full_req_db.status == FullProjectAnalysisStatus.COMPLETED:
            # Đếm findings cho full scan
            findings_severities_full = db.query(AnalysisFinding.severity)\
                                         .filter(AnalysisFinding.full_project_analysis_request_id == full_req_db.id)\
                                         .all()
            for severity_tuple in findings_severities_full:
                severity_enum_member = severity_tuple[0]
                if severity_enum_member == PyAnalysisSeverity.ERROR: errors_full += 1
                elif severity_enum_member == PyAnalysisSeverity.WARNING: warnings_full += 1
                else: others_full += 1
        
        report_url_full_scan_str = None
        try:
            # Tạo URL cho route mới sẽ được định nghĩa bên dưới
            report_url_full_scan_str = str(request.url_for('ui_full_scan_report_get', request_id_param=full_req_db.id))
        except Exception as e:
            logger.warning(f"Could not generate report URL for full scan {full_req_db.id}: {e}")

        analysis_history.append(
            AnalysisHistoryItem(
                id=full_req_db.id, scan_type="full",
                identifier=f"Branch: {full_req_db.branch_name}", title=f"Full Scan - {full_req_db.branch_name}",
                status=full_req_db.status.value, # Truyền string value của Enum
                requested_at=full_req_db.requested_at, report_url=report_url_full_scan_str,
                total_errors=errors_full, total_warnings=warnings_full, total_other_findings=others_full
            )
        )

    analysis_history.sort(key=lambda item: item.requested_at, reverse=True)
    analysis_history = analysis_history[:20] # Giới hạn số lượng hiển thị

    return templates.TemplateResponse("pages/projects/project_detail.html", {
        "request": request,
        "page_title": f"Project: {project_details.repo_name}",
        "current_user": current_user,
        "project": project_details,
        "analysis_history_items": analysis_history, # Đã được cập nhật
        "current_year": datetime.now().year
    })
    
@ui_report_router.get("/full-scan/{request_id_param}/report", response_class=HTMLResponse, name="ui_full_scan_report_get")
async def ui_full_scan_report_page(
    request: Request,
    request_id_param: int,
    db: Session = Depends(get_db),
    current_user: Optional[auth_schemas.UserPublic] = Depends(get_current_ui_user)
):
    if not current_user:
        flash_messages = request.session.get("_flash_messages", [])
        flash_messages.append({"category": "warning", "message": "Please login to view reports."})
        request.session["_flash_messages"] = flash_messages
        return RedirectResponse(url=request.url_for("ui_login_get"), status_code=status.HTTP_302_FOUND)

    logger.info(f"User {current_user.email} requesting Full Scan Report for Request ID: {request_id_param}")

    # Lấy FullProjectAnalysisRequest từ DB
    full_scan_request_db = crud_full_scan.get_full_scan_request_by_id(db, request_id=request_id_param)
    
    if not full_scan_request_db:
        flash_messages = request.session.get("_flash_messages", [])
        flash_messages.append({"category": "error", "message": "Full Project Scan Request not found."})
        request.session["_flash_messages"] = flash_messages
        return RedirectResponse(url=request.url_for("ui_dashboard_get"), status_code=status.HTTP_302_FOUND)

    # Kiểm tra quyền sở hữu project
    project_of_scan = project_crud.get_project_by_id(db, project_id=full_scan_request_db.project_id, user_id=current_user.id)
    if not project_of_scan:
        flash_messages = request.session.get("_flash_messages", [])
        flash_messages.append({"category": "error", "message": "You do not have permission to view this scan report."})
        request.session["_flash_messages"] = flash_messages
        return RedirectResponse(url=request.url_for("ui_dashboard_get"), status_code=status.HTTP_302_FOUND)

    # Lấy danh sách findings cho Full Scan Request này
    # finding_crud.get_findings_by_request_id đã được cập nhật để có thể query theo full_project_analysis_request_id (cần kiểm tra lại)
    # Hoặc tạo một hàm mới/cập nhật hàm cũ
    # Giả sử crud_finding.get_findings_by_full_scan_request_id(db, full_scan_id)
    
    # Hiện tại, chúng ta sẽ query trực tiếp ở đây để rõ ràng
    all_findings_for_full_scan = db.query(AnalysisFinding)\
                                    .filter(AnalysisFinding.full_project_analysis_request_id == request_id_param)\
                                    .order_by(AnalysisFinding.severity, AnalysisFinding.file_path, AnalysisFinding.line_start)\
                                    .all()
    
    project_level_findings = [f for f in all_findings_for_full_scan if f.finding_level == PyFindingLevel.PROJECT or f.finding_level == PyFindingLevel.MODULE]
    granular_findings = [f for f in all_findings_for_full_scan if f.finding_level == PyFindingLevel.FILE]

    # Lấy project_summary từ error_message của full_scan_request_db (như đã lưu ở worker)
    # Hoặc nếu bạn đã thêm trường `summary` riêng thì dùng trường đó.
    project_summary_from_db = full_scan_request_db.error_message if full_scan_request_db.status == FullProjectAnalysisStatus.COMPLETED else None
    if full_scan_request_db.status == FullProjectAnalysisStatus.FAILED and full_scan_request_db.error_message:
        # Nếu failed, error_message thực sự là lỗi, không phải summary
        project_summary_from_db = f"Analysis failed: {full_scan_request_db.error_message}"


    return templates.TemplateResponse(
        "pages/reports/full_scan_report.html", # Template mới sẽ được tạo ở bước sau
        {
            "request": request,
            "page_title": f"Full Scan Report: {project_of_scan.repo_name} ({full_scan_request_db.branch_name})",
            "current_user": current_user,
            "project": project_of_scan, 
            "scan_request_details": full_scan_request_db, # Chi tiết của FullProjectAnalysisRequest
            "project_summary": project_summary_from_db, # Summary từ LLM (nếu có)
            "project_level_findings": project_level_findings,
            "granular_findings": granular_findings,
            "current_year": datetime.now().year
        }
    )

@ui_project_router.get("/{project_id}/settings", response_class=HTMLResponse, name="ui_project_settings_get")
async def project_settings_page_ui_get(
    request: Request,
    project_id: int, # Path parameter
    db: Session = Depends(get_db),
    current_user: Optional[auth_schemas.UserPublic] = Depends(get_current_ui_user)
):
    if not current_user:
        flash_messages = request.session.get("_flash_messages", [])
        flash_messages.append({"category": "warning", "message": "Please login to access project settings."})
        request.session["_flash_messages"] = flash_messages
        return RedirectResponse(url=request.url_for("ui_login_get"), status_code=status.HTTP_302_FOUND)

    logger.info(f"Serving project settings page for project ID: {project_id}, user: {current_user.email}")

    project = project_crud.get_project_by_id(db, project_id=project_id, user_id=current_user.id)
    if not project:
        logger.warning(f"Project settings: Project ID {project_id} not found or not owned by user {current_user.email}.")
        flash_messages = request.session.get("_flash_messages", [])
        flash_messages.append({"category": "error", "message": "Project not found or you do not have access to its settings."})
        request.session["_flash_messages"] = flash_messages
        return RedirectResponse(url=request.url_for("ui_dashboard_get"), status_code=status.HTTP_302_FOUND)

    return templates.TemplateResponse("pages/projects/project_settings.html", {
        "request": request,
        "page_title": f"Settings: {project.repo_name}",
        "current_user": current_user,
        "project": project, # Truyền project object (SQLAlchemy model)
        "current_year": datetime.now().year
    })

@ui_project_router.post("/{project_id}/settings", response_class=RedirectResponse, name="ui_project_settings_post")
async def project_settings_page_ui_post(
    request: Request,
    project_id: int, # Path parameter
    # Form data
    repo_name: str = Form(...), # Bạn có thể quyết định cho phép sửa tên repo hay không
    main_branch: str = Form(...),
    language: Optional[str] = Form(None),
    custom_project_notes: Optional[str] = Form(None),
    # Dependencies
    db: Session = Depends(get_db),
    current_user: Optional[auth_schemas.UserPublic] = Depends(get_current_ui_user)
):
    if not current_user:
        # Mặc dù get_current_ui_user là Optional, nhưng POST request thường yêu cầu user phải đăng nhập
        # Nếu không, redirect về login
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    logger.info(f"Processing update for project ID: {project_id} by user: {current_user.email}")

    # Lấy project hiện tại để đảm bảo user sở hữu nó trước khi cập nhật
    # và để có thể lấy github_repo_id (nếu repo_name không được phép sửa và không được gửi từ form)
    existing_project = project_crud.get_project_by_id(db, project_id=project_id, user_id=current_user.id)
    if not existing_project:
        flash_messages = request.session.get("_flash_messages", [])
        flash_messages.append({"category": "error", "message": "Project not found or you do not have permission to update."})
        request.session["_flash_messages"] = flash_messages
        return RedirectResponse(url=request.url_for("ui_dashboard_get"), status_code=status.HTTP_302_FOUND)


    project_update_data = project_schemas.ProjectUpdate(
        # Nếu không cho sửa repo_name từ form, bạn có thể bỏ nó khỏi ProjectUpdate
        # hoặc lấy giá trị hiện tại từ existing_project.repo_name
        # Tạm thời, giả sử repo_name được gửi từ form và có thể thay đổi
        repo_name=repo_name,
        main_branch=main_branch,
        language=language if language and language.strip() else None, # Đảm bảo None nếu là chuỗi rỗng
        custom_project_notes=custom_project_notes if custom_project_notes and custom_project_notes.strip() else None
    )
    
    updated_project = project_crud.update_project(
        db, project_id=project_id, project_in=project_update_data, user_id=current_user.id
    )
    
    flash_messages = request.session.get("_flash_messages", [])
    if updated_project:
        flash_messages.append({"category": "success", "message": "Project settings updated successfully."})
    else:
        # Điều này không nên xảy ra nếu get_project_by_id ở trên đã thành công
        flash_messages.append({"category": "error", "message": "Failed to update project settings."})
    request.session["_flash_messages"] = flash_messages
    
    # Redirect về trang chi tiết dự án sau khi cập nhật
    return RedirectResponse(url=request.url_for("ui_project_detail_get", project_id_path=project_id), status_code=status.HTTP_302_FOUND)

@ui_report_router.get("/pr-analysis/{request_id_param}/report", response_class=HTMLResponse, name="ui_pr_report_get")
async def ui_pr_analysis_report_page(
    request: Request,
    request_id_param: int,
    db: Session = Depends(get_db), # Sử dụng Session đồng bộ nếu CRUD functions của bạn đồng bộ
    current_user: auth_schemas.UserPublic = Depends(get_current_ui_user) # Đảm bảo dependency này đúng
):
    if not current_user:
        # ... (xử lý redirect nếu chưa login)
        flash_messages = request.session.get("_flash_messages", [])
        flash_messages.append({"category": "warning", "message": "Please login to view reports."})
        request.session["_flash_messages"] = flash_messages
        return RedirectResponse(url=request.url_for("ui_login_get"), status_code=status.HTTP_302_FOUND)

    # Lấy PRAnalysisRequest từ DB
    # pr_crud.get_pr_analysis_request_by_id là hàm bạn đã có trong app/webhook_service/crud_pr_analysis.py
    pr_analysis_db_obj = pr_crud.get_pr_analysis_request_by_id(db, request_id=request_id_param)
    
    if not pr_analysis_db_obj:
        # ... (xử lý nếu không tìm thấy PR Analysis Request)
        flash_messages = request.session.get("_flash_messages", [])
        flash_messages.append({"category": "error", "message": "PR Analysis Request not found."})
        request.session["_flash_messages"] = flash_messages
        return RedirectResponse(url=request.url_for("ui_dashboard_get"), status_code=status.HTTP_302_FOUND)

    # Kiểm tra quyền sở hữu project
    # project_crud.get_project_by_id là hàm bạn đã có trong app/project_service/crud_project.py
    project_of_pr = project_crud.get_project_by_id(db, project_id=pr_analysis_db_obj.project_id, user_id=current_user.id)
    if not project_of_pr:
        # ... (xử lý nếu không có quyền)
        flash_messages = request.session.get("_flash_messages", [])
        flash_messages.append({"category": "error", "message": "You do not have permission to view this project's report."})
        request.session["_flash_messages"] = flash_messages
        return RedirectResponse(url=request.url_for("ui_dashboard_get"), status_code=status.HTTP_302_FOUND)

    # Lấy danh sách findings cho PR Analysis Request này
    # finding_crud.get_findings_by_request_id là hàm bạn đã có trong app/analysis_module/crud_finding.py
    findings_db_list = finding_crud.get_findings_by_request_id(db, pr_analysis_request_id=request_id_param)
    
    # Truyền trực tiếp các model objects (SQLAlchemy) vào template.
    # Jinja2 có thể truy cập các thuộc tính của chúng.
    return templates.TemplateResponse(
        "pages/reports/pr_analysis_report.html", # Đảm bảo đường dẫn này đúng
        {
            "request": request,
            "page_title": f"PR Analysis: {pr_analysis_db_obj.pr_title or f'PR #{pr_analysis_db_obj.pr_number}'}",
            "current_user": current_user,
            "project": project_of_pr, 
            "pr_request_details": pr_analysis_db_obj, 
            "findings": findings_db_list, 
            "current_year": datetime.now().year 
            # "SeverityLevel": SeverityLevel, # Nếu bạn muốn dùng Enum SeverityLevel trong template cho CSS class chẳng hạn
        }
    )

@ui_project_router.post("/{project_id_path}/delete", name="ui_delete_project_post")
async def delete_project_ui_post(
    request: Request,
    project_id_path: int,
    db: Session = Depends(get_db),
    current_user: auth_schemas.UserPublic = Depends(get_current_ui_user)
):
    if not current_user:
        # ... (redirect về login) ...
        flash_messages = request.session.get("_flash_messages", [])
        flash_messages.append({"category": "warning", "message": "Please login to delete projects."})
        request.session["_flash_messages"] = flash_messages
        return RedirectResponse(url=request.url_for("ui_login_get"), status_code=status.HTTP_302_FOUND)

    logger.info(f"UI: User {current_user.email} attempting to delete project ID: {project_id_path}")
    
    # Gọi logic xóa project (bao gồm cả xóa webhook trên GitHub)
    # Đây là nơi chúng ta có thể gọi API endpoint DELETE /api/projects/{project_id}
    # Hoặc tái sử dụng logic xóa tương tự như trong API endpoint đó.
    # Để đơn giản và tránh lặp code, chúng ta có thể gọi API bằng HTTP client nội bộ.
    # Tuy nhiên, để giữ logic tập trung, có thể tạo một "service function" để cả API và UI route cùng gọi.

    # Tạm thời, tái sử dụng logic tương tự như API endpoint:
    project_to_delete_info = project_crud.get_project_by_id(db, project_id=project_id_path, user_id=current_user.id)

    flash_messages = request.session.get("_flash_messages", [])

    if not project_to_delete_info:
        logger.warning(f"UI: Project ID {project_id_path} for deletion not found or not owned by user {current_user.email}.")
        flash_messages.append({"category": "error", "message": "Project not found or you do not have permission."})
        request.session["_flash_messages"] = flash_messages
        return RedirectResponse(url=request.url_for("ui_dashboard_get"), status_code=status.HTTP_302_FOUND)

    repo_name_hook_del = project_to_delete_info.repo_name
    gh_webhook_id_hook_del = project_to_delete_info.github_webhook_id
    user_gh_token: Optional[str] = None

    if gh_webhook_id_hook_del:
        user_db_for_token = db.query(User).filter(User.id == current_user.id).first()
        if user_db_for_token and user_db_for_token.github_access_token_encrypted:
            user_gh_token = decrypt_data(user_db_for_token.github_access_token_encrypted)
        
        if user_gh_token and repo_name_hook_del:
            delete_gh_hook_url = f"https://api.github.com/repos/{repo_name_hook_del}/hooks/{gh_webhook_id_hook_del}"
            gh_headers = {
                "Authorization": f"token {user_gh_token}", 
                "Accept": "application/vnd.github.v3+json",
                "X-GitHub-Api-Version": "2022-11-28"
            }
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.delete(delete_gh_hook_url, headers=gh_headers)
                    if response.status_code == 204:
                        logger.info(f"UI: Successfully deleted GitHub webhook {gh_webhook_id_hook_del} for project '{repo_name_hook_del}'.")
                    elif response.status_code == 404:
                        logger.warning(f"UI: GitHub webhook {gh_webhook_id_hook_del} not found for project '{repo_name_hook_del}'.")
                    else:
                        logger.error(f"UI: Error deleting GitHub webhook {gh_webhook_id_hook_del} for '{repo_name_hook_del}': {response.status_code} - {response.text[:100]}")
                        flash_messages.append({"category": "warning", "message": f"Could not delete webhook from GitHub (Code: {response.status_code}). Project deleted from NovaGuard."})
            except Exception as e_ui_hook_del:
                logger.exception(f"UI: Unexpected error deleting GitHub webhook for '{repo_name_hook_del}'.")
                flash_messages.append({"category": "warning", "message": "Error contacting GitHub to delete webhook. Project deleted from NovaGuard."})
        elif gh_webhook_id_hook_del:
            flash_messages.append({"category": "info", "message": "Could not retrieve GitHub token to delete webhook. Project deleted from NovaGuard."})


    # Xóa project khỏi DB NovaGuard
    deleted_project = project_crud.delete_project(db=db, project_id=project_id_path, user_id=current_user.id)

    if deleted_project:
        logger.info(f"UI: Project ID {project_id_path} ('{deleted_project.repo_name}') deleted from DB by user {current_user.email}.")
        # Chỉ thêm success message nếu không có warning nào về webhook được thêm trước đó
        if not any(fm["category"] == "warning" for fm in flash_messages) and \
           not any(fm["category"] == "error" for fm in flash_messages):
            flash_messages.append({"category": "success", "message": f"Project '{deleted_project.repo_name}' and its associated data have been successfully deleted."})
    else:
        # Lỗi này không nên xảy ra nếu get_project_by_id ở trên thành công
        flash_messages.append({"category": "error", "message": "An error occurred while trying to delete the project from NovaGuard."})
    
    request.session["_flash_messages"] = flash_messages
    return RedirectResponse(url=request.url_for("ui_dashboard_get"), status_code=status.HTTP_302_FOUND)

app.include_router(ui_pages_router)
app.include_router(ui_auth_router)
app.include_router(ui_project_router)
app.include_router(ui_report_router)

if settings.DEBUG:
    logger.info("="*50)
    logger.info("REGISTERED ROUTES (main.py):")
    unique_paths_with_methods = {}
    for route in app.routes:
        if hasattr(route, "path"):
            path = route.path
            name = getattr(route, "name", "unnamed_route")
            methods = getattr(route, "methods", "NO_METHODS")
            
            # Tạo một key duy nhất cho path và methods để tránh in lặp lại cho cùng một endpoint
            # do cách FastAPI/Starlette xử lý route cho HEAD method.
            route_key = f"{path}_{str(sorted(list(methods)) if methods else 'NONE')}"

            if route_key not in unique_paths_with_methods:
                unique_paths_with_methods[route_key] = True
                logger.info(f"  Name: {name}, Path: {path}, Methods: {methods}, Class: {type(route)}")
                if name == "ui_add_project_get":
                    logger.info(f"    Specifics for '{name}': Path Format: {getattr(route, 'path_format', route.path)}")
    logger.info("="*50)

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