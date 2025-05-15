import hashlib
import hmac
import logging
from typing import Dict, Any

from fastapi import APIRouter, Depends, HTTPException, status, Request, Header
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.config import settings
from app.project_service import crud_project # Để tìm project từ repo_id
from app.webhook_service import schemas_pr_analysis as pr_schemas # Schemas cho PR và Webhook
from app.webhook_service import crud_pr_analysis # CRUD cho PRAnalysisRequest
from app.common.message_queue.kafka_producer import send_pr_analysis_task # Kafka producer
from app.models import User, Project 

router = APIRouter()
logger = logging.getLogger(__name__)

# --- Webhook Signature Verification ---
async def verify_github_signature(request: Request, x_hub_signature_256: str | None = Header(None)):
    """
    Xác thực chữ ký webhook từ GitHub.
    GitHub gửi chữ ký trong header X-Hub-Signature-256 (hoặc X-Hub-Signature cho SHA1).
    Chữ ký được tạo bằng HMAC SHA256 của payload request body với webhook secret.
    """
    if not settings.GITHUB_WEBHOOK_SECRET:
        logger.warning("GITHUB_WEBHOOK_SECRET is not configured. Skipping signature verification (NOT SECURE FOR PRODUCTION).")
        # Trong production, bạn NÊN raise HTTPException nếu secret không được cấu hình.
        # raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Webhook secret not configured.")
        return # Bỏ qua xác thực nếu không có secret (chỉ cho dev)

    if not x_hub_signature_256:
        logger.error("X-Hub-Signature-256 header is missing!")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="X-Hub-Signature-256 header is missing!")

    request_body = await request.body() # Đọc raw body của request
    
    # Tạo hash từ body request với secret
    hashed_payload = hmac.new(
        settings.GITHUB_WEBHOOK_SECRET.encode('utf-8'),
        request_body,
        hashlib.sha256
    ).hexdigest()
    
    expected_signature = f"sha256={hashed_payload}"
    
    if not hmac.compare_digest(expected_signature, x_hub_signature_256):
        logger.error(f"Request signature mismatch. Expected: {expected_signature}, Got: {x_hub_signature_256}")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid request signature.")
    
    logger.info("GitHub webhook signature verified successfully.")


# --- Webhook Endpoint ---
@router.post("/github", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(verify_github_signature)])
async def handle_github_webhook(
    payload: pr_schemas.GitHubWebhookPayload, # Pydantic model để parse payload
    request: Request, # Request object để có thể đọc raw body nếu cần (đã dùng trong verify_signature)
    x_github_event: str | None = Header(None), # Event type từ GitHub (e.g., "pull_request")
    db: Session = Depends(get_db)
):
    """
    Endpoint để nhận webhook từ GitHub.
    - Xác thực chữ ký (qua dependency).
    - Chỉ xử lý sự kiện 'pull_request' với action 'opened' hoặc 'synchronize'.
    - Tìm project tương ứng.
    - Tạo PRAnalysisRequest.
    - Đẩy task vào Kafka.
    """
    logger.info(f"Received GitHub webhook event: {x_github_event}, action: {payload.action}")

    # 1. Chỉ xử lý sự kiện pull_request với các action cụ thể
    if x_github_event != "pull_request":
        logger.info(f"Ignoring event type: {x_github_event}")
        return {"message": "Event type ignored"}

    if payload.action not in ["opened", "synchronize", "reopened"]:
        logger.info(f"Ignoring pull_request action: {payload.action}")
        return {"message": "Pull request action ignored"}

    if not payload.pull_request or not payload.repository:
        logger.warning("Webhook payload missing pull_request or repository information.")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing pull_request or repository data in payload")

    # 2. Trích xuất thông tin cần thiết từ payload
    repo_id_from_github = str(payload.repository.id) # GitHub repo ID (thường là số, chuyển sang str để khớp DB)
    pr_data = payload.pull_request
    
    pr_number = pr_data.number
    pr_title = pr_data.title
    pr_html_url = pr_data.html_url
    head_sha = pr_data.head.sha
    # diff_url = pr_data.diff_url # diff_url có thể dùng trực tiếp, hoặc tự build từ html_url + .diff

    logger.info(f"Processing PR #{pr_number} ('{pr_title}') for repo ID {repo_id_from_github}, head SHA: {head_sha}")

    # 3. Tìm Project trong DB dựa trên github_repo_id
    # Lưu ý: crud_project.get_project_by_github_repo_id cần được tạo ra
    # Hoặc chúng ta có thể query tất cả project có repo_id đó và tìm user để lấy token
    # Hiện tại, chúng ta cần project_id của NovaGuard và user's github_access_token
    
    # Giả sử chúng ta có một hàm để lấy project và user dựa trên repo_id_from_github
    # Đây là phần cần được thiết kế cẩn thận:
    # Một repo GitHub (repo_id_from_github) có thể được thêm bởi nhiều user vào NovaGuard.
    # Webhook là chung cho repo, vậy chúng ta nên xử lý cho user nào?
    # - Cách 1: Nếu GitHub App được dùng, installation_id sẽ giúp xác định.
    # - Cách 2 (MVP với OAuth): Webhook được user cấu hình cho repo của họ.
    #            Khi project được thêm vào NovaGuard, chúng ta lưu user_id.
    #            Cần một cách để map repo_id_from_github về (project_id của NovaGuard, user_id)
    #            Có thể `Projects.github_repo_id` không phải là unique toàn cục, mà là unique theo `user_id`.
    #            Schema DB hiện tại có `UNIQUE(user_id, github_repo_id)`
    
    # Cho MVP, giả sử chúng ta tìm Project đầu tiên khớp với repo_id_from_github.
    # Điều này có thể cần cải thiện sau này nếu nhiều user quản lý cùng 1 repo qua NovaGuard.
    # Cần tạo hàm này trong project_service/crud_project.py
    # db_project = crud_project.get_project_by_github_repo_id_first(db, github_repo_id=repo_id_from_github)
    
    # Tạm thời, để đơn giản, giả sử webhook chỉ được thiết lập cho một project cụ thể trong NovaGuard
    # và chúng ta cần tìm Project đó.
    # Cách tiếp cận thực tế hơn:
    # Khi user thêm project và thiết lập webhook, webhook URL của NovaGuard có thể chứa project_id
    # hoặc một định danh duy nhất. GitHub không hỗ trợ điều này trực tiếp trong payload.
    #
    # Với GitHub App, `installation.id` trong payload sẽ giúp.
    #
    # Với OAuth App và webhook do user tự tạo:
    # Chúng ta cần tìm tất cả `Project` records trong DB có `github_repo_id` này.
    # Rồi xử lý cho từng record đó (tức là tạo PRAnalysisRequest cho mỗi user đã thêm project đó).
    
    projects_in_novaguard = db.query(User, Project).join(Project, User.id == Project.user_id).filter(Project.github_repo_id == repo_id_from_github).all()

    if not projects_in_novaguard:
        logger.warning(f"No project found in NovaGuard matching GitHub repo ID: {repo_id_from_github}")
        # Không nên raise lỗi cho GitHub, chỉ log và bỏ qua, vì webhook có thể được cấu hình cho repo chưa thêm.
        return {"message": "Project not found in NovaGuard"}

    tasks_created_count = 0
    for user_model, project_model in projects_in_novaguard:
        if not user_model.github_access_token_encrypted: # Hoặc một cách khác để kiểm tra token còn hợp lệ
            logger.warning(f"User {user_model.email} (ID: {user_model.id}) for project {project_model.repo_name} (ID: {project_model.id}) does not have a GitHub access token. Skipping PR analysis.")
            continue

        # 4. Tạo bản ghi PRAnalysisRequest
        pr_request_in = pr_schemas.PRAnalysisRequestCreate(
            project_id=project_model.id, # ID của project trong DB NovaGuard
            pr_number=pr_number,
            pr_title=pr_title,
            pr_github_url=pr_html_url,
            head_sha=head_sha,
            status=pr_schemas.PRAnalysisStatus.PENDING
        )
        db_pr_analysis_request = crud_pr_analysis.create_pr_analysis_request(db, request_in=pr_request_in)
        logger.info(f"Created PRAnalysisRequest ID: {db_pr_analysis_request.id} for project ID: {project_model.id}")

        # 5. Chuẩn bị message và gửi vào Kafka
        # QUAN TRỌNG: Vấn đề bảo mật khi truyền github_access_token.
        # Cho MVP, chúng ta có thể truyền nó, nhưng cần mã hóa hoặc có cơ chế an toàn hơn trong tương lai.
        # Giải mã token ở đây (cần hàm giải mã) hoặc worker sẽ lấy từ DB dựa trên user_id/project_id.
        # Tốt nhất là worker tự lấy token từ DB khi cần.
        
        # Lấy token đã mã hóa. Worker sẽ cần user_id để lấy token này và giải mã.
        # Hoặc, nếu worker xử lý ngay, có thể giải mã ở đây và truyền token thật (ít an toàn hơn).
        # Hiện tại, chúng ta sẽ để worker tự xử lý việc lấy token.
        
        kafka_task_data = {
            "pr_analysis_request_id": db_pr_analysis_request.id,
            "project_id": project_model.id, # NovaGuard project ID
            "user_id": user_model.id, # User ID để worker có thể lấy token của user
            "github_repo_id": repo_id_from_github, # GitHub repo ID
            "pr_number": pr_number,
            "head_sha": head_sha,
            "diff_url": str(pr_data.diff_url), # URL để lấy file .diff
            "task_type": "pr_analysis"
            # "target_branch": pr_data.base.ref # Nhánh đích, có thể hữu ích cho context
        }
        
        # Gửi message không đồng bộ
        success = await send_pr_analysis_task(kafka_task_data)
        if success:
            logger.info(f"Task for PRAnalysisRequest ID {db_pr_analysis_request.id} sent to Kafka.")
            tasks_created_count += 1
        else:
            logger.error(f"Failed to send task for PRAnalysisRequest ID {db_pr_analysis_request.id} to Kafka.")
            # Cập nhật status của db_pr_analysis_request thành FAILED?
            # crud_pr_analysis.update_pr_analysis_request_status(db, db_pr_analysis_request.id, pr_schemas.PRAnalysisStatus.FAILED, "Kafka send error")


    if tasks_created_count > 0:
        return {"message": f"Webhook processed. {tasks_created_count} analysis task(s) queued."}
    else:
        logger.info(f"No analysis tasks were queued for PR #{pr_number} in repo {repo_id_from_github}.")
        return {"message": "Webhook processed, but no analysis tasks were queued (e.g., user token missing)."}