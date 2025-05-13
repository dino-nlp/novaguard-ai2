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
from app.models import User, Project # Đảm bảo Project được import
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


@router.delete("/{project_id}", response_model=project_schemas_module.ProjectPublic) # Hoặc trả về status code 204 No Content
async def delete_existing_project(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: auth_schemas_module.UserPublic = Depends(get_current_active_user)
):
    logger.info(f"User {current_user.email} (ID: {current_user.id}) attempting to delete project ID: {project_id}")
    
    project_to_delete_db_info = crud_project.get_project_by_id(db, project_id, current_user.id) # Đổi tên biến
    
    if not project_to_delete_db_info:
        logger.warning(f"Failed to delete project ID {project_id}: Not found or not owned by user {current_user.email}.")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found or not owned by user to delete")

    github_token_for_delete: Optional[str] = None # Đổi tên biến
    if project_to_delete_db_info.github_webhook_id:
        db_user_for_token_del = db.query(User).filter(User.id == current_user.id).first() # Đổi tên biến
        if db_user_for_token_del and db_user_for_token_del.github_access_token_encrypted:
            github_token_for_delete = decrypt_data(db_user_for_token_del.github_access_token_encrypted)

    deleted_project_obj = crud_project.delete_project(db=db, project_id=project_id, user_id=current_user.id) # Đổi tên biến

    if deleted_project_obj and project_to_delete_db_info.github_webhook_id and github_token_for_delete:
        logger.info(f"Attempting to delete webhook ID {project_to_delete_db_info.github_webhook_id} for project '{project_to_delete_db_info.repo_name}' on GitHub.")
        delete_hook_url = f"https://api.github.com/repos/{project_to_delete_db_info.repo_name}/hooks/{project_to_delete_db_info.github_webhook_id}"
        headers_gh_del = { # Đổi tên biến
            "Authorization": f"token {github_token_for_delete}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        async with httpx.AsyncClient(timeout=10.0) as client: # Thêm timeout
            try:
                del_hook_response = await client.delete(delete_hook_url, headers=headers_gh_del)
                if del_hook_response.status_code == 204:
                    logger.info(f"Successfully deleted webhook {project_to_delete_db_info.github_webhook_id} from GitHub for project '{project_to_delete_db_info.repo_name}'.")
                elif del_hook_response.status_code == 404:
                    logger.warning(f"Webhook {project_to_delete_db_info.github_webhook_id} not found on GitHub for project '{project_to_delete_db_info.repo_name}'. It might have been deleted manually.")
                else:
                    del_hook_response.raise_for_status()
            except httpx.HTTPStatusError as e_del_hook: # Đổi tên biến
                logger.error(f"GitHub API error when deleting webhook {project_to_delete_db_info.github_webhook_id} for '{project_to_delete_db_info.repo_name}': {e_del_hook.response.status_code} - {e_del_hook.response.text[:200]}")
            except Exception as e_unexp_del_hook: # Đổi tên biến
                logger.exception(f"Unexpected error deleting GitHub webhook for '{project_to_delete_db_info.repo_name}'. Error: {e_unexp_del_hook}")
    elif project_to_delete_db_info and project_to_delete_db_info.github_webhook_id and not github_token_for_delete:
        logger.warning(f"Could not retrieve GitHub token for user {current_user.email}. Webhook {project_to_delete_db_info.github_webhook_id} for project '{project_to_delete_db_info.repo_name}' was not deleted from GitHub.")

    logger.info(f"Project ID {project_id} (Name: '{deleted_project_obj.repo_name if deleted_project_obj else 'N/A'}') processing for deletion completed by user {current_user.email}.")
    # Trả về thông tin project đã bị xóa
    if deleted_project_obj:
        return deleted_project_obj
    else:
        # Điều này không nên xảy ra nếu get_project_by_id ở trên đã tìm thấy nó
        # Trả về thông tin đã lấy trước khi xóa nếu crud_project.delete_project không trả về gì sau commit
        # Hoặc raise lỗi nếu không muốn trả về dữ liệu "cũ"
        logger.error(f"Project {project_id} was found but delete operation returned None. This is unexpected.")
        # Để an toàn, trả về thông tin đã lấy trước đó, nhưng đây là dấu hiệu có thể có vấn đề
        return project_to_delete_db_info