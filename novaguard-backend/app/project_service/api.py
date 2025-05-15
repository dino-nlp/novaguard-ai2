import logging
import httpx # Cần cho việc gọi API GitHub
from typing import List, Optional, Dict, Any # Thêm Dict, Any
from datetime import datetime, timezone # Thêm timezone

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel as PydanticBaseModel, HttpUrl 

from app.core.db import get_db
from app.core.config import settings
from app.core.security import decrypt_data
from app.models import User, Project, FullProjectAnalysisStatus, FullProjectAnalysisRequest
from sqlalchemy import func

# Import schemas từ project_service và auth_service, dùng bí danh để rõ ràng
from app.project_service import schemas as project_schemas_module
from app.project_service import crud_project
from app.auth_service import schemas as auth_schemas_module
from app.auth_service.auth_bearer import get_current_active_user # Dependency xác thực

# Import thêm cho các API mới (nếu bạn đã thêm chúng ở đây)
from app.webhook_service import schemas_pr_analysis # Giả sử bạn đặt tên là schemas_pr_analysis
from app.webhook_service import crud_pr_analysis # Đổi tên pr_crud nếu trùng
from app.analysis_module import schemas_finding as finding_schemas
from app.analysis_module import crud_finding as finding_crud

from . import crud_full_scan
from app.common.message_queue.kafka_producer import send_pr_analysis_task 


router = APIRouter()
logger = logging.getLogger(__name__)

# --- Schema cho GitHub Repo (đã được cập nhật ở schemas.py để có default_branch) ---
class GitHubRepoSchema(PydanticBaseModel): 
    id: int
    name: str
    full_name: str
    private: bool
    html_url: HttpUrl
    description: Optional[str] = None
    updated_at: datetime
    default_branch: Optional[str] = None

    class Config:
        from_attributes = True


# --- Helper Functions để fetch và format GitHub Repos ---
async def _fetch_raw_github_repositories_for_user(github_token: str, client: httpx.AsyncClient) -> List[Dict[str, Any]]:
    """
    Hàm helper để lấy danh sách thô các repositories từ GitHub API, xử lý phân trang.
    Trả về list của các dicts là dữ liệu repo thô.
    """
    all_repos_data: List[Dict[str, Any]] = []
    github_repos_url = "https://api.github.com/user/repos"
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    # Lấy tất cả các loại repo mà user có quyền truy cập (owner, collaborator, organization member)
    # Sắp xếp theo lần cập nhật gần nhất để các repo hoạt động nhiều sẽ lên đầu
    params = {"type": "all", "sort": "updated", "per_page": 100} 
    
    current_url: Optional[str] = github_repos_url
    page_num = 1

    while current_url:
        logger.debug(f"Fetching GitHub repos page {page_num} from URL: {current_url} with params: {params if page_num == 1 and current_url == github_repos_url else None}")
        try:
            # Chỉ truyền params cho request đầu tiên, các request sau đã có params trong current_url từ Link header
            request_params_internal = params if page_num == 1 and current_url == github_repos_url else None
            response = await client.get(current_url, headers=headers, params=request_params_internal, timeout=20.0) # Thêm timeout
            response.raise_for_status()
            page_data = response.json()
            if not isinstance(page_data, list):
                logger.error(f"Unexpected response format from GitHub (expected list, got {type(page_data)}): {str(page_data)[:500]}")
                break 
            all_repos_data.extend(page_data)
            
            current_url = None 
            if 'Link' in response.headers:
                links_header = httpx.Headers(response.headers).get_list('Link')
                for link_str_item in links_header:
                    parts = link_str_item.split(';')
                    if len(parts) == 2:
                        url_part = parts[0].strip('<>')
                        rel_part = parts[1].strip()
                        if 'rel="next"' in rel_part:
                            current_url = url_part
                            page_num += 1
                            break 
        except httpx.HTTPStatusError as e_status:
            logger.error(f"GitHub API Error (Page {page_num}): {e_status.response.status_code} - {e_status.request.url} - Response: {e_status.response.text[:500]}")
            raise # Re-throw để hàm gọi bên ngoài xử lý
        except httpx.RequestError as e_req: # Bắt lỗi kết nối, timeout
            logger.error(f"GitHub API Request Error (Page {page_num}): {e_req.request.url} - {e_req}")
            raise
        except Exception as e_general:
            logger.exception(f"Unexpected error while fetching GitHub repositories (Page {page_num}).")
            raise 
            
    return all_repos_data

async def get_formatted_github_repos_from_api_data(raw_repos_data: List[Dict[str, Any]]) -> List[GitHubRepoSchema]:
    """
    Hàm helper để chuyển đổi dữ liệu repo thô từ GitHub API sang list các GitHubRepoSchema.
    """
    formatted_repos: List[GitHubRepoSchema] = []
    for repo_data in raw_repos_data:
        if not isinstance(repo_data, dict): 
            logger.warning(f"Skipping non-dict item in raw_repos_data: {repo_data}")
            continue
        try:
            updated_at_str = repo_data.get("updated_at")
            parsed_updated_at = None
            if updated_at_str:
                try:
                    parsed_updated_at = datetime.fromisoformat(updated_at_str.replace("Z", "+00:00"))
                except ValueError:
                    logger.warning(f"Could not parse 'updated_at' date '{updated_at_str}' for repo '{repo_data.get('full_name')}'. Using current time as fallback.")
            
            # Đảm bảo các trường bắt buộc có giá trị trước khi tạo schema
            repo_id = repo_data.get("id")
            repo_name_short = repo_data.get("name")
            repo_full_name = repo_data.get("full_name")
            repo_private = repo_data.get("private")
            repo_html_url = repo_data.get("html_url")

            if not all([repo_id, repo_name_short, repo_full_name, repo_private is not None, repo_html_url]):
                logger.warning(f"Skipping repo due to missing essential fields: id={repo_id}, name={repo_name_short}, full_name={repo_full_name}, private={repo_private}, html_url={repo_html_url}")
                continue

            formatted_repos.append(GitHubRepoSchema(
                id=repo_id,
                name=repo_name_short,
                full_name=repo_full_name,
                private=repo_private,
                html_url=repo_html_url,
                description=repo_data.get("description"),
                updated_at=parsed_updated_at or datetime.now(timezone.utc),
                default_branch=repo_data.get("default_branch")
            ))
        except Exception as e_format:
            logger.warning(f"Could not parse repo data for '{repo_data.get('full_name', 'N/A')}': {e_format}. Skipping this repo.")
    return formatted_repos

async def get_github_repos_for_user_logic(user_in_db: User, db: Session) -> List[GitHubRepoSchema]: # Đổi tên schema thành GitHubRepoSchema
    """
    Logic cốt lõi để lấy và định dạng danh sách repo GitHub cho một user.
    Hàm này có thể được gọi từ API endpoint và từ route UI.
    """
    if not user_in_db.github_access_token_encrypted:
        logger.info(f"User {user_in_db.email} has not connected GitHub account or token is missing (logic function).")
        return []

    github_token = decrypt_data(user_in_db.github_access_token_encrypted)
    if not github_token:
        logger.error(f"Failed to decrypt GitHub token for user {user_in_db.email} (logic function).")
        return [] # Quan trọng: Trả về list rỗng nếu không giải mã được token

    raw_repos_data: List[Dict[str, Any]] = []
    try:
        async with httpx.AsyncClient() as client: # Đảm bảo client được tạo ở đây
            raw_repos_data = await _fetch_raw_github_repositories_for_user(github_token, client)
    except httpx.HTTPStatusError as e_http:
        if e_http.response.status_code in [401, 403]:
            logger.warning(f"GitHub token for user {user_in_db.email} is invalid, expired, or lacks permissions during _fetch_raw: {e_http.response.status_code}")
        else:
            logger.error(f"HTTP error in _fetch_raw_github_repositories_for_user for {user_in_db.email}: {e_http}")
        return [] 
    except Exception as e_fetch_logic: # Bắt các lỗi khác từ _fetch_raw...
        logger.exception(f"Unexpected error in _fetch_raw_github_repositories_for_user for {user_in_db.email}: {e_fetch_logic}")
        return []

    formatted_repos = await get_formatted_github_repos_from_api_data(raw_repos_data)
    logger.info(f"Helper logic successfully fetched and formatted {len(formatted_repos)} repositories for user {user_in_db.email}.")
    return formatted_repos


# --- API Endpoints ---

@router.get("/github-repos", response_model=List[GitHubRepoSchema], summary="List user's GitHub repositories")
async def list_user_github_repositories(
    db: Session = Depends(get_db),
    current_user_from_token: auth_schemas_module.UserPublic = Depends(get_current_active_user)
):
    logger.info(f"API Endpoint: Fetching GitHub repositories for user: {current_user_from_token.email} (ID: {current_user_from_token.id})")
    
    db_user = db.query(User).filter(User.id == current_user_from_token.id).first()
    if not db_user:
        logger.error(f"User ID {current_user_from_token.id} from token not found in DB.")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User associated with token not found.")

    # Gọi hàm helper logic
    # Hàm helper đã xử lý trường hợp token không có hoặc giải mã lỗi bằng cách trả về list rỗng.
    # API endpoint có thể quyết định raise lỗi nếu muốn hành vi chặt chẽ hơn.
    try:
        repos = await get_github_repos_for_user_logic(db_user, db)
    except Exception as e_api_logic_call: 
        logger.exception(f"API Endpoint: Unexpected error calling get_github_repos_for_user_logic for {db_user.email}: {e_api_logic_call}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Could not fetch repositories from GitHub due to an unexpected server error.")

    # Xử lý trường hợp token không hợp lệ (ví dụ, đã bị thu hồi trên GitHub)
    # Hàm get_github_repos_for_user_logic sẽ log lỗi và trả về list rỗng.
    # Nếu muốn API endpoint này báo lỗi rõ ràng hơn cho client:
    if not repos and db_user.github_access_token_encrypted:
        # Nếu có token mã hóa mà không lấy được repo, có thể token đã hết hạn/bị thu hồi
        # Kiểm tra lại lần nữa việc giải mã token (để phân biệt với trường hợp user không có repo nào)
        temp_token_check = decrypt_data(db_user.github_access_token_encrypted)
        if not temp_token_check: # Lỗi giải mã (không nên xảy ra nếu get_github_repos_for_user_logic đã chạy)
             logger.error(f"API Endpoint: GitHub token decryption failed for user {db_user.email} (redundant check).")
             # Lỗi này nên được bắt bởi get_github_repos_for_user_logic rồi
        else:
            # Token giải mã được nhưng không lấy được repo (có thể token hết hạn trên GitHub, hoặc không có repo)
            logger.warning(f"API Endpoint: Returning empty list for {db_user.email}. Token might be invalid on GitHub or user has no accessible repos.")
            # Không raise lỗi ở đây, để client tự xử lý list rỗng. Hoặc có thể raise 403.
            # raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Failed to fetch repositories. GitHub token may be invalid or expired, or user has no repositories.")


    return repos


@router.post("/", response_model=project_schemas_module.ProjectPublic, status_code=status.HTTP_201_CREATED)
async def create_new_project(
    project_in: project_schemas_module.ProjectCreate,
    db: Session = Depends(get_db),
    current_user: auth_schemas_module.UserPublic = Depends(get_current_active_user)
):
    logger.info(f"User {current_user.email} (ID: {current_user.id}) attempting to create project: '{project_in.repo_name}' (GitHub Repo ID: {project_in.github_repo_id})")

    db_project = crud_project.create_project(db=db, project_in=project_in, user_id=current_user.id)
    if not db_project:
        logger.warning(f"Failed to create project '{project_in.repo_name}' for user {current_user.email}. Possible conflict (user_id, github_repo_id).")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Project with this GitHub Repo ID already exists for this user, or another DB error occurred.",
        )
    logger.info(f"Project '{db_project.repo_name}' (NovaGuard ID: {db_project.id}) created in DB for user {current_user.email}.")

    db_user_full = db.query(User).filter(User.id == current_user.id).first()
    if not db_user_full or not db_user_full.github_access_token_encrypted:
        # Không rollback project, nhưng thông báo webhook không thể tạo
        logger.warning(f"Project {db_project.repo_name} created, but user {current_user.email} has no GitHub token for webhook setup.")
        # Không raise lỗi, trả về project đã tạo nhưng có thể kèm thông báo trong response nếu muốn
        # Hoặc client UI sẽ kiểm tra github_webhook_id
        return db_project # Trả về project đã tạo, client có thể kiểm tra github_webhook_id

    github_token = decrypt_data(db_user_full.github_access_token_encrypted)
    if not github_token:
        logger.error(f"Project {db_project.repo_name} created, but GitHub token decryption failed for user {current_user.email}. Webhook not created.")
        return db_project

    if '/' not in project_in.repo_name: # repo_name nên là owner/repo
        logger.error(f"Invalid repo_name format for webhook creation: '{project_in.repo_name}'. Expected 'owner/repo'. Webhook not created.")
        return db_project 

    if not settings.NOVAGUARD_PUBLIC_URL:
        logger.error(f"Project {db_project.repo_name} created, but NOVAGUARD_PUBLIC_URL is not set. Webhook setup skipped.")
        return db_project

    webhook_payload_url = f"{settings.NOVAGUARD_PUBLIC_URL.rstrip('/')}/api/webhooks/github"
    
    github_hook_data = {
        "name": "web",
        "active": True,
        "events": ["pull_request"], # Chỉ sự kiện pull_request
        "config": {
            "url": webhook_payload_url,
            "content_type": "json",
            "secret": settings.GITHUB_WEBHOOK_SECRET
        }
    }
    
    # Sử dụng db_project.repo_name (đã lưu trong DB) để tạo hook URL
    create_hook_url = f"https://api.github.com/repos/{db_project.repo_name}/hooks" 
    headers_gh = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    webhook_id_on_github: Optional[str] = None
    async with httpx.AsyncClient(timeout=15.0) as client: # Tăng timeout
        try:
            logger.info(f"Attempting to create webhook for '{db_project.repo_name}' on GitHub. URL: {create_hook_url}, PayloadURL: {webhook_payload_url}")
            hook_response = await client.post(create_hook_url, json=github_hook_data, headers=headers_gh)
            
            if hook_response.status_code == 201: # Created
                created_hook_data = hook_response.json()
                webhook_id_on_github = str(created_hook_data.get("id"))
                logger.info(f"Successfully created webhook (ID: {webhook_id_on_github}) for project '{db_project.repo_name}' on GitHub.")
            elif hook_response.status_code == 422: # Unprocessable Entity (thường là hook đã tồn tại)
                response_data = hook_response.json()
                if "errors" in response_data and any("Hook already exists" in error.get("message", "") for error in response_data.get("errors",[])):
                    logger.warning(f"Webhook already exists for '{db_project.repo_name}'. Consider fetching existing hooks to find and store its ID if not already present.")
                    # Bạn có thể thêm logic để tìm hook đã tồn tại và lấy ID của nó ở đây
                else:
                    logger.error(f"Failed to create webhook for '{db_project.repo_name}' (Status 422 - Unprocessable): {hook_response.text}")
            elif hook_response.status_code == 404: # Not Found (repo không tồn tại trên GitHub hoặc token không có quyền)
                logger.error(f"Failed to create webhook for '{db_project.repo_name}' (Status 404 - Not Found). Repo may not exist or token lacks permission. Response: {hook_response.text}")
            else:
                hook_response.raise_for_status() # Raise lỗi cho các status code khác
        
        except httpx.HTTPStatusError as e:
            error_message_detail = e.response.json().get('message', 'Unknown GitHub API error') if e.response.content else str(e)
            logger.error(f"HTTP error creating GitHub webhook for '{db_project.repo_name}': {e.response.status_code} - {error_message_detail}")
        except Exception as e_unexpected_hook:
            logger.exception(f"Unexpected error creating GitHub webhook for '{db_project.repo_name}'. Error: {e_unexpected_hook}")

    if webhook_id_on_github:
        db_project.github_webhook_id = webhook_id_on_github
        try:
            db.commit()
            db.refresh(db_project)
            logger.info(f"Updated project {db_project.id} in DB with GitHub webhook ID: {webhook_id_on_github}")
        except Exception as e_db_save_hook: # Đổi tên biến
            db.rollback()
            logger.exception(f"Failed to save webhook_id {webhook_id_on_github} for project {db_project.id}. Webhook created on GitHub but not saved in DB. Error: {e_db_save_hook}")
    else:
        logger.warning(f"Webhook ID not obtained or not saved for project {db_project.id} ('{db_project.repo_name}'). Check GitHub or server logs.")

    return db_project


@router.get("/", response_model=project_schemas_module.ProjectList)
async def read_user_projects(
    db: Session = Depends(get_db),
    current_user: auth_schemas_module.UserPublic = Depends(get_current_active_user),
    skip: int = Query(0, ge=0, description="Number of items to skip"),
    limit: int = Query(10, ge=1, le=100, description="Number of items to return per page")
):
    logger.info(f"Fetching projects for user {current_user.email} (ID: {current_user.id}), skip: {skip}, limit: {limit}")
    projects_db = crud_project.get_projects_by_user(db=db, user_id=current_user.id, skip=skip, limit=limit)
    
    total_projects_count = db.query(func.count(Project.id)).filter(Project.user_id == current_user.id).scalar() # Đổi tên biến
    if total_projects_count is None: total_projects_count = 0
        
    logger.info(f"Found {len(projects_db)} projects for user {current_user.email} in current page, total: {total_projects_count}.")
    return {"projects": projects_db, "total": total_projects_count}


@router.get("/{project_id}", response_model=project_schemas_module.ProjectPublic)
async def read_project_details( # API này đã có
    project_id: int,
    db: Session = Depends(get_db),
    current_user: auth_schemas_module.UserPublic = Depends(get_current_active_user)
):
    logger.info(f"Fetching details for project ID: {project_id} for user {current_user.email} (ID: {current_user.id})")
    db_project = crud_project.get_project_by_id(db=db, project_id=project_id, user_id=current_user.id)
    if db_project is None:
        logger.warning(f"Project ID {project_id} not found or not owned by user {current_user.email}.")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found or not owned by user")
    logger.info(f"Successfully fetched details for project ID: {project_id}")
    return db_project

# API Lấy danh sách PR Analysis Requests cho một Project
class PRAnalysisRequestListResponse(PydanticBaseModel):
    items: List[schemas_pr_analysis.PRAnalysisRequestItem] # Sử dụng schema đã tạo
    total: int

@router.get("/{project_id}/pr-analysis-requests", response_model=PRAnalysisRequestListResponse, summary="List PR Analysis Requests for a Project")
async def list_pr_analysis_requests_for_project(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: auth_schemas_module.UserPublic = Depends(get_current_active_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100)
):
    logger.info(f"User {current_user.email} requesting PR analysis list for project ID: {project_id}")
    db_project_check = crud_project.get_project_by_id(db, project_id=project_id, user_id=current_user.id)
    if not db_project_check:
        logger.warning(f"Project ID {project_id} not found or not owned by user {current_user.email} for PR list.")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found or not owned by user")

    # Sử dụng pr_crud (từ webhook_service.crud_pr_analysis)
    requests_db = pr_crud.get_pr_analysis_requests_by_project_id(db, project_id=project_id, skip=skip, limit=limit)
    total_requests = pr_crud.count_pr_analysis_requests_by_project_id(db, project_id=project_id)
    return {"items": requests_db, "total": total_requests}

# API Lấy chi tiết một PR Analysis Request và các Findings
# (Schema PRAnalysisReportDetail nên được định nghĩa trong finding_schemas.py hoặc một module chung)
# Giả sử nó đã được định nghĩa trong finding_schemas:
# class PRAnalysisReportDetail(PydanticBaseModel):
#     pr_request_details: webhook_service.schemas_pr_analysis.PRAnalysisRequestPublic
#     findings: List[finding_schemas.AnalysisFindingPublic]

@router.get("/pr-analysis-requests/{request_id_param}", response_model=finding_schemas.PRAnalysisReportDetail, summary="Get PR Analysis Report Details")
async def get_pr_analysis_report_details( # Đổi tên request_id thành request_id_param để tránh xung đột nếu có biến request
    request_id_param: int, # Sửa tên biến ở đây
    db: Session = Depends(get_db),
    current_user: auth_schemas_module.UserPublic = Depends(get_current_active_user)
):
    logger.info(f"User {current_user.email} requesting report for PR Analysis ID: {request_id_param}")
    # Sử dụng pr_crud (từ webhook_service.crud_pr_analysis)
    pr_request_db_obj = pr_crud.get_pr_analysis_request_by_id(db, request_id=request_id_param) # Sửa tên biến
    if not pr_request_db_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PR Analysis Request not found")

    project_owner_check = db.query(Project.user_id).filter(Project.id == pr_request_db_obj.project_id).scalar()
    if project_owner_check != current_user.id:
         raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have access to this PR analysis request.")

    # Sử dụng finding_crud (từ analysis_module.crud_finding)
    findings_db_list = finding_crud.get_findings_by_request_id(db, pr_analysis_request_id=request_id_param) # Sửa tên biến
    return {"pr_request_details": pr_request_db_obj, "findings": findings_db_list}


@router.put("/{project_id}", response_model=project_schemas_module.ProjectPublic)
async def update_existing_project(
    project_id: int,
    project_in: project_schemas_module.ProjectUpdate,
    db: Session = Depends(get_db),
    current_user: auth_schemas_module.UserPublic = Depends(get_current_active_user)
):
    logger.info(f"User {current_user.email} (ID: {current_user.id}) attempting to update project ID: {project_id} with data: {project_in.model_dump(exclude_unset=True)}")
    updated_project_db = crud_project.update_project( # Đổi tên biến
        db=db, project_id=project_id, project_in=project_in, user_id=current_user.id
    )
    if updated_project_db is None:
        logger.warning(f"Failed to update project ID {project_id}: Not found or not owned by user {current_user.email}.")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found or not owned by user to update")
    logger.info(f"Project ID {project_id} updated successfully by user {current_user.email}.")
    return updated_project_db

@router.delete(
    "/{project_id}", 
    response_model=project_schemas_module.ProjectPublic, # Trả về thông tin project đã xóa
    summary="Delete a project",
    status_code=status.HTTP_200_OK # Hoặc 204 No Content nếu không trả về body
)
async def delete_existing_project_api( # Đổi tên hàm để rõ ràng là API
    project_id: int,
    db: Session = Depends(get_db),
    current_user: auth_schemas_module.UserPublic = Depends(get_current_active_user)
):
    logger.info(f"API: User {current_user.email} (ID: {current_user.id}) attempting to delete project ID: {project_id}")
    
    # 1. Lấy thông tin project và kiểm tra quyền sở hữu
    # Hàm get_project_by_id đã kiểm tra user_id
    project_to_delete_db = crud_project.get_project_by_id(db, project_id=project_id, user_id=current_user.id)
    
    if not project_to_delete_db:
        logger.warning(f"API: Failed to delete project ID {project_id}. Not found or not owned by user {current_user.email}.")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="Project not found or you do not have permission to delete it."
        )

    # Lưu lại thông tin cần cho việc xóa webhook TRƯỚC KHI xóa project khỏi DB
    repo_name_for_hook_deletion = project_to_delete_db.repo_name
    github_webhook_id_for_deletion = project_to_delete_db.github_webhook_id
    
    # 2. Cố gắng xóa Webhook trên GitHub nếu có
    github_token_for_delete_hook: Optional[str] = None
    if github_webhook_id_for_deletion:
        # Lấy token của user để gọi GitHub API
        # Cần query User model đầy đủ từ current_user.id
        user_db_for_token = db.query(User).filter(User.id == current_user.id).first()
        if user_db_for_token and user_db_for_token.github_access_token_encrypted:
            github_token_for_delete_hook = decrypt_data(user_db_for_token.github_access_token_encrypted)
        
        if github_token_for_delete_hook and repo_name_for_hook_deletion:
            delete_hook_url = f"https://api.github.com/repos/{repo_name_for_hook_deletion}/hooks/{github_webhook_id_for_deletion}"
            headers_gh_delete_hook = {
                "Authorization": f"token {github_token_for_delete_hook}",
                "Accept": "application/vnd.github.v3+json",
                "X-GitHub-Api-Version": "2022-11-28"
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                try:
                    logger.info(f"API: Attempting to delete webhook ID {github_webhook_id_for_deletion} for project '{repo_name_for_hook_deletion}' on GitHub.")
                    hook_delete_response = await client.delete(delete_hook_url, headers=headers_gh_delete_hook)
                    
                    if hook_delete_response.status_code == 204: # No Content - Success
                        logger.info(f"API: Successfully deleted webhook {github_webhook_id_for_deletion} from GitHub for project '{repo_name_for_hook_deletion}'.")
                    elif hook_delete_response.status_code == 404: # Not Found
                        logger.warning(f"API: Webhook {github_webhook_id_for_deletion} not found on GitHub for project '{repo_name_for_hook_deletion}'. It might have been deleted manually or never existed with this ID.")
                    else:
                        # Log lỗi nhưng vẫn tiếp tục xóa project khỏi DB NovaGuard
                        logger.error(f"API: GitHub API error when deleting webhook {github_webhook_id_for_deletion} for '{repo_name_for_hook_deletion}': {hook_delete_response.status_code} - {hook_delete_response.text[:200]}")
                        # Không raise HTTPException ở đây để việc xóa project trong DB vẫn diễn ra
                except Exception as e_gh_hook_delete:
                    logger.exception(f"API: Unexpected error deleting GitHub webhook for '{repo_name_for_hook_deletion}'. Error: {e_gh_hook_delete}")
        elif github_webhook_id_for_deletion: # Có webhook ID nhưng không lấy được token hoặc repo_name
            logger.warning(f"API: Could not delete webhook {github_webhook_id_for_deletion} for project '{repo_name_for_hook_deletion}' due to missing GitHub token or repo name.")

    # 3. Xóa project khỏi DB NovaGuard
    deleted_project_from_db = crud_project.delete_project(db=db, project_id=project_id, user_id=current_user.id)
    
    # crud_project.delete_project sẽ trả về project đã xóa (trước khi commit)
    # hoặc None nếu không tìm thấy (đã kiểm tra ở trên)
    if deleted_project_from_db:
        logger.info(f"API: Project ID {project_id} (Name: '{deleted_project_from_db.repo_name}') successfully deleted from NovaGuard DB by user {current_user.email}.")
        # Trả về thông tin project vừa bị xóa
        return project_schemas_module.ProjectPublic.from_orm(deleted_project_from_db)
    else:
        # Trường hợp này không nên xảy ra nếu logic ở trên đúng
        logger.error(f"API: Project {project_id} was confirmed to exist but delete operation in DB returned None. This is unexpected.")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error deleting project from database after initial checks.")


@router.post(
    "/{project_id}/full-scan",
    response_model=project_schemas_module.FullProjectAnalysisRequestPublic, # Sửa lại nếu tên schema khác
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger a Full Project Scan"
)
async def trigger_full_project_scan(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: auth_schemas_module.UserPublic = Depends(get_current_active_user) # get_current_active_user đã có
):
    logger.info(f"User {current_user.email} triggering full project scan for project ID: {project_id}")
    db_project = crud_project.get_project_by_id(db, project_id=project_id, user_id=current_user.id)
    if not db_project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found or not owned by user")

    # Kiểm tra xem có scan nào đang chạy không (tùy chọn, để tránh spam)
    existing_scans = db.query(FullProjectAnalysisRequest).filter(
        FullProjectAnalysisRequest.project_id == project_id,
        FullProjectAnalysisRequest.status.in_([
            FullProjectAnalysisStatus.PENDING, FullProjectAnalysisStatus.PROCESSING,
            FullProjectAnalysisStatus.SOURCE_FETCHED, FullProjectAnalysisStatus.CKG_BUILDING,
            FullProjectAnalysisStatus.ANALYZING
        ])
    ).first()
    if existing_scans:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A full project scan is already in progress or pending for this project (Request ID: {existing_scans.id}, Status: {existing_scans.status.value})."
        )


    scan_request = crud_full_scan.create_full_scan_request(
        db=db, project_id=db_project.id, branch_name=db_project.main_branch
    )
    logger.info(f"Created FullProjectAnalysisRequest ID: {scan_request.id} for project {db_project.repo_name}, branch {db_project.main_branch}")

    # Gửi message vào Kafka
    # Cần một topic riêng cho full scan hoặc một trường để phân biệt task_type
    # Ví dụ: thêm "task_type": "full_project_scan"
    kafka_task_data = {
        "task_type": "full_project_scan", # Để worker phân biệt
        "full_project_analysis_request_id": scan_request.id,
        "project_id": db_project.id,
        "user_id": current_user.id, # Để worker lấy GitHub token
        "github_repo_id": db_project.github_repo_id, # ID repo trên GitHub
        "repo_full_name": db_project.repo_name, # Ví dụ: "owner/repo"
        "branch_to_scan": db_project.main_branch,
    }

    # Sử dụng lại KAFKA_PR_ANALYSIS_TOPIC nhưng với task_type khác,
    # hoặc tạo KAFKA_FULL_SCAN_TOPIC mới
    # Hiện tại, giả sử dùng chung topic và phân biệt bằng task_type
    success = await send_pr_analysis_task(kafka_task_data) # send_pr_analysis_task có thể cần đổi tên thành send_analysis_task
    if success:
        logger.info(f"Task for FullProjectAnalysisRequest ID {scan_request.id} sent to Kafka.")
    else:
        logger.error(f"Failed to send task for FullProjectAnalysisRequest ID {scan_request.id} to Kafka.")
        # Cập nhật status của scan_request thành FAILED? Hoặc để worker xử lý nếu không nhận được
        crud_full_scan.update_full_scan_request_status(db, scan_request.id, FullProjectAnalysisStatus.FAILED, "Kafka send error")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to queue analysis task.")

    return scan_request