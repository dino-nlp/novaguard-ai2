import logging
from pathlib import Path
from typing import Optional, List, Dict, Any
import httpx
from urllib.parse import urlencode
import secrets
from datetime import datetime

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

from app.analysis_module import crud_finding as finding_crud
from app.analysis_module import schemas_finding as finding_schemas

from app.core.db import get_db
from app.models import User, Project, PRAnalysisRequest, AnalysisFinding


logger = logging.getLogger("main_app")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(levelname)s [%(name)s:%(lineno)s] - %(message)s')
    handler.setFormatter(formatter)
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