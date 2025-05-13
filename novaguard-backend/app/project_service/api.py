import logging
import httpx # Cần cho việc gọi API GitHub
from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel as PydanticBaseModel, HttpUrl 

from app.core.db import get_db
from app.core.config import settings
from app.core.security import decrypt_data
from app.models import User, Project
from sqlalchemy import func

# Import schemas từ project_service và auth_service, dùng bí danh để rõ ràng
from app.project_service import schemas as project_schemas_module
from app.project_service import crud_project
from app.auth_service import schemas as auth_schemas_module
from app.auth_service.auth_bearer import get_current_active_user # Dependency xác thực

router = APIRouter()
logger = logging.getLogger(__name__)

# --- Schema cho GitHub Repo (để trả về cho frontend) ---
# Định nghĩa schema này ở đây vì nó được sử dụng và trả về bởi API này.
# Hoặc có thể đặt trong project_schemas_module nếu muốn.
class GitHubRepoSchema(PydanticBaseModel): # Sử dụng PydanticBaseModel đã import
    id: int
    name: str
    full_name: str # owner/repo
    private: bool
    html_url: HttpUrl # Sử dụng HttpUrl đã import từ pydantic
    description: Optional[str] = None
    updated_at: datetime

# --- API Endpoints ---

@router.get("/github-repos", response_model=List[GitHubRepoSchema], summary="List user's GitHub repositories")
async def list_user_github_repositories(
    db: Session = Depends(get_db),
    current_user: auth_schemas_module.UserPublic = Depends(get_current_active_user)
):
    """
    Lấy danh sách các repositories từ tài khoản GitHub đã kết nối của người dùng.
    Bao gồm xử lý phân trang của GitHub API.
    """
    logger.info(f"Fetching GitHub repositories for user: {current_user.email} (ID: {current_user.id})")
    db_user = db.query(User).filter(User.id == current_user.id).first()
    if not db_user or not db_user.github_access_token_encrypted:
        logger.warning(f"User {current_user.email} has not connected their GitHub account or token is missing.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="GitHub account not connected or access token not found for this user. Please connect via /auth/github."
        )

    github_token = decrypt_data(db_user.github_access_token_encrypted)
    if not github_token:
        logger.error(f"Failed to decrypt GitHub token for user {current_user.email}. Check FERNET_KEY and token integrity.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not retrieve GitHub access token due to a server error."
        )

    github_repos_url = "https://api.github.com/user/repos"
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28" # Khuyến nghị bởi GitHub
    }
    params = {"type": "all", "sort": "updated", "per_page": 100} 
    
    all_repos_data = []
    current_url: Optional[str] = github_repos_url
    page_num = 1

    async with httpx.AsyncClient() as client:
        while current_url:
            logger.debug(f"Fetching GitHub repos page {page_num} from URL: {current_url} with params: {params if page_num == 1 else None}")
            try:
                # Chỉ truyền params cho request đầu tiên, các request sau đã có params trong current_url từ Link header
                request_params = params if page_num == 1 and current_url == github_repos_url else None
                response = await client.get(current_url, headers=headers, params=request_params)
                response.raise_for_status()
                page_data = response.json()
                if not isinstance(page_data, list):
                    logger.error(f"Unexpected response format from GitHub (expected list): {page_data}")
                    raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Unexpected response format from GitHub.")
                all_repos_data.extend(page_data)
                
                current_url = None 
                if 'Link' in response.headers:
                    links = httpx.Headers(response.headers).get_list('Link')
                    for link_str in links:
                        parts = link_str.split(';')
                        if len(parts) == 2:
                            url_part = parts[0].strip('<>')
                            rel_part = parts[1].strip()
                            if 'rel="next"' in rel_part:
                                current_url = url_part
                                page_num += 1
                                break 
            except httpx.HTTPStatusError as e:
                logger.error(f"Error fetching GitHub repositories for user {current_user.email} (Page {page_num}): {e.response.status_code} - {e.response.text}")
                if e.response.status_code in [401, 403]:
                    raise HTTPException(status_code=e.response.status_code, detail="GitHub token is invalid, expired, or lacks necessary permissions. Please reconnect your GitHub account.")
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Failed to fetch repositories from GitHub: {e.response.json().get('message', 'Service error')}")
            except Exception as e:
                logger.exception(f"Unexpected error fetching GitHub repositories (Page {page_num}).")
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Could not fetch repositories from GitHub due to an unexpected error.")
    
    formatted_repos = []
    for repo in all_repos_data:
        if not isinstance(repo, dict): continue
        try:
            # Chuyển đổi updated_at sang đối tượng datetime timezone-aware
            updated_at_str = repo.get("updated_at")
            parsed_updated_at = None
            if updated_at_str:
                try:
                    # Thử parse với định dạng Z (UTC)
                    parsed_updated_at = datetime.fromisoformat(updated_at_str.replace("Z", "+00:00"))
                except ValueError:
                    logger.warning(f"Could not parse 'updated_at' date '{updated_at_str}' for repo '{repo.get('full_name')}'. Skipping this field for this repo.")

            formatted_repos.append(GitHubRepoSchema(
                id=repo["id"],
                name=repo["name"],
                full_name=repo["full_name"],
                private=repo["private"],
                html_url=repo["html_url"],
                description=repo.get("description"),
                updated_at=parsed_updated_at or datetime.now(timezone.utc) # Fallback nếu parse lỗi
            ))
        except Exception as e:
            logger.warning(f"Could not parse repo data for '{repo.get('full_name', 'N/A')}': {e}. Skipping this repo.")
            
    logger.info(f"Successfully fetched {len(formatted_repos)} repositories for user {current_user.email}.")
    return formatted_repos


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
        db.delete(db_project) 
        db.commit()
        logger.error(f"Project {db_project.repo_name} creation rolled back as user {current_user.email} has no GitHub token for webhook setup.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="GitHub account not connected or access token not found. Cannot create webhook. Please connect your GitHub account first."
        )

    github_token = decrypt_data(db_user_full.github_access_token_encrypted)
    if not github_token:
        db.delete(db_project)
        db.commit()
        logger.error(f"Project {db_project.repo_name} creation rolled back due to GitHub token decryption failure for user {current_user.email}.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not retrieve GitHub access token for webhook setup due to a server error."
        )

    if '/' not in project_in.repo_name:
        db.delete(db_project)
        db.commit()
        logger.error(f"Invalid repo_name format for webhook creation: '{project_in.repo_name}'. Expected 'owner/repo'.")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid repository name format. Must be 'owner/repo'.")

    if not settings.NOVAGUARD_PUBLIC_URL:
        db.delete(db_project)
        db.commit()
        logger.error(f"Project {db_project.repo_name} creation rolled back as NOVAGUARD_PUBLIC_URL is not set in server settings.")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server misconfiguration: Public URL for webhooks is not set.")

    webhook_payload_url = f"{settings.NOVAGUARD_PUBLIC_URL.rstrip('/')}/webhooks/github"
    
    github_hook_data = {
        "name": "web",
        "active": True,
        "events": ["pull_request"],
        "config": {
            "url": webhook_payload_url,
            "content_type": "json",
            "secret": settings.GITHUB_WEBHOOK_SECRET # Đảm bảo secret này được cấu hình đúng
        }
    }
    
    create_hook_url = f"https://api.github.com/repos/{project_in.repo_name}/hooks"
    headers_gh = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    webhook_id_on_github: Optional[str] = None
    async with httpx.AsyncClient() as client:
        try:
            logger.info(f"Attempting to create webhook for '{project_in.repo_name}' on GitHub. URL: {create_hook_url}, PayloadURL: {webhook_payload_url}")
            hook_response = await client.post(create_hook_url, json=github_hook_data, headers=headers_gh)
            
            if hook_response.status_code == 201:
                created_hook_data = hook_response.json()
                webhook_id_on_github = str(created_hook_data.get("id"))
                logger.info(f"Successfully created webhook (ID: {webhook_id_on_github}) for project '{db_project.repo_name}' on GitHub.")
            elif hook_response.status_code == 422:
                response_data = hook_response.json()
                if "errors" in response_data and any("Hook already exists on this repository" in error.get("message", "") for error in response_data["errors"]):
                    logger.warning(f"Webhook already exists for '{project_in.repo_name}'. Attempting to find existing hook (not fully implemented).")
                    # TODO: Implement logic to fetch existing hooks and find the one matching our payload URL and events
                    # to retrieve its ID if we want to reuse/confirm it.
                    # For now, we'll assume if it exists, we might not get an ID back easily this way.
                else:
                    logger.error(f"Failed to create webhook for '{project_in.repo_name}' (Status 422 - Unprocessable): {hook_response.text}")
            else:
                hook_response.raise_for_status()
        
        except httpx.HTTPStatusError as e:
            error_message_detail = e.response.json().get('message', 'Unknown GitHub API error') if e.response.content else str(e)
            logger.error(f"HTTP error creating GitHub webhook for '{project_in.repo_name}': {e.response.status_code} - {error_message_detail}")
            # Project đã được tạo, nhưng webhook lỗi. Không rollback project, để user có thể thử lại hoặc cấu hình thủ công.
            # Hoặc có thể raise lỗi để báo cho user:
            # raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Failed to create webhook on GitHub: {error_message_detail}")
        except Exception as e:
            logger.exception(f"Unexpected error creating GitHub webhook for '{project_in.repo_name}'.")

    if webhook_id_on_github:
        db_project.github_webhook_id = webhook_id_on_github
        try:
            db.commit()
            db.refresh(db_project)
            logger.info(f"Updated project {db_project.id} in DB with GitHub webhook ID: {webhook_id_on_github}")
        except Exception as e:
            db.rollback()
            logger.exception(f"Failed to save webhook_id {webhook_id_on_github} for project {db_project.id}. Webhook created on GitHub but not saved in DB.")
            # Đây là trạng thái không nhất quán, cần xử lý cẩn thận.
    else:
        logger.warning(f"Webhook ID not obtained for project {db_project.id} ('{db_project.repo_name}'). Check GitHub or server logs.")

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
    
    total_projects = db.query(func.count(Project.id)).filter(Project.user_id == current_user.id).scalar()
    if total_projects is None: total_projects = 0
        
    logger.info(f"Found {len(projects_db)} projects for user {current_user.email} in current page, total: {total_projects}.")
    return {"projects": projects_db, "total": total_projects}


@router.get("/{project_id}", response_model=project_schemas_module.ProjectPublic)
async def read_project_details(
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


@router.put("/{project_id}", response_model=project_schemas_module.ProjectPublic)
async def update_existing_project(
    project_id: int,
    project_in: project_schemas_module.ProjectUpdate,
    db: Session = Depends(get_db),
    current_user: auth_schemas_module.UserPublic = Depends(get_current_active_user)
):
    logger.info(f"User {current_user.email} (ID: {current_user.id}) attempting to update project ID: {project_id} with data: {project_in.model_dump(exclude_unset=True)}")
    updated_project = crud_project.update_project(
        db=db, project_id=project_id, project_in=project_in, user_id=current_user.id
    )
    if updated_project is None:
        logger.warning(f"Failed to update project ID {project_id}: Not found or not owned by user {current_user.email}.")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found or not owned by user to update")
    logger.info(f"Project ID {project_id} updated successfully by user {current_user.email}.")
    return updated_project


@router.delete("/{project_id}", response_model=project_schemas_module.ProjectPublic)
async def delete_existing_project(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: auth_schemas_module.UserPublic = Depends(get_current_active_user)
):
    logger.info(f"User {current_user.email} (ID: {current_user.id}) attempting to delete project ID: {project_id}")
    
    project_to_delete_info = crud_project.get_project_by_id(db, project_id, current_user.id)
    
    if not project_to_delete_info:
        logger.warning(f"Failed to delete project ID {project_id}: Not found or not owned by user {current_user.email}.")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found or not owned by user to delete")

    # Lấy GitHub token trước khi project bị xóa khỏi DB (nếu cần để xóa webhook)
    github_token: Optional[str] = None
    if project_to_delete_info.github_webhook_id: # Chỉ cần token nếu có webhook_id để xóa
        db_user_full = db.query(User).filter(User.id == current_user.id).first()
        if db_user_full and db_user_full.github_access_token_encrypted:
            github_token = decrypt_data(db_user_full.github_access_token_encrypted)

    # Xóa project khỏi DB NovaGuard trước
    deleted_project_from_db = crud_project.delete_project(db=db, project_id=project_id, user_id=current_user.id)
    # delete_project của CRUD sẽ trả về object vừa xóa (trước khi commit xóa) hoặc None nếu không tìm thấy
    # Nhưng ở đây ta đã kiểm tra ở trên, nên nó sẽ không None nếu đến được đây.

    # Nếu xóa project khỏi DB thành công và có thông tin để xóa webhook trên GitHub
    if deleted_project_from_db and project_to_delete_info.github_webhook_id and github_token:
        logger.info(f"Attempting to delete webhook ID {project_to_delete_info.github_webhook_id} for project '{project_to_delete_info.repo_name}' on GitHub.")
        delete_hook_url = f"https://api.github.com/repos/{project_to_delete_info.repo_name}/hooks/{project_to_delete_info.github_webhook_id}"
        headers_gh = {
            "Authorization": f"token {github_token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        async with httpx.AsyncClient() as client:
            try:
                del_hook_response = await client.delete(delete_hook_url, headers=headers_gh)
                if del_hook_response.status_code == 204: # No Content - Success
                    logger.info(f"Successfully deleted webhook {project_to_delete_info.github_webhook_id} from GitHub for project '{project_to_delete_info.repo_name}'.")
                elif del_hook_response.status_code == 404: # Not Found
                    logger.warning(f"Webhook {project_to_delete_info.github_webhook_id} not found on GitHub for project '{project_to_delete_info.repo_name}'. It might have been deleted manually.")
                else:
                    del_hook_response.raise_for_status() # Raise for other errors
            except httpx.HTTPStatusError as e:
                logger.error(f"GitHub API error when deleting webhook {project_to_delete_info.github_webhook_id} for '{project_to_delete_info.repo_name}': {e.response.status_code} - {e.response.text}")
            except Exception as e:
                logger.exception(f"Unexpected error deleting GitHub webhook for '{project_to_delete_info.repo_name}'.")
    elif project_to_delete_info and project_to_delete_info.github_webhook_id and not github_token:
        logger.warning(f"Could not retrieve GitHub token for user {current_user.email}. Webhook {project_to_delete_info.github_webhook_id} for project '{project_to_delete_info.repo_name}' was not deleted from GitHub.")


    logger.info(f"Project ID {project_id} (Name: '{deleted_project_from_db.repo_name if deleted_project_from_db else 'N/A'}') processing for deletion completed by user {current_user.email}.")
    # Trả về thông tin project đã bị xóa khỏi DB
    return deleted_project_from_db if deleted_project_from_db else project_to_delete_info