from sqlalchemy.orm import Session

from app.models import PRAnalysisRequest, PRAnalysisStatus
from app.webhook_service.schemas_pr_analysis import PRAnalysisRequestCreate
from sqlalchemy import func
from typing import List

def create_pr_analysis_request(db: Session, request_in: PRAnalysisRequestCreate) -> PRAnalysisRequest:
    """
    Tạo một bản ghi PRAnalysisRequest mới trong DB.
    """
    db_request = PRAnalysisRequest(
        project_id=request_in.project_id,
        pr_number=request_in.pr_number,
        pr_title=request_in.pr_title,
        pr_github_url=str(request_in.pr_github_url) if request_in.pr_github_url else None, # Chuyển HttpUrl sang str
        head_sha=request_in.head_sha,
        status=request_in.status # Sẽ là PRAnalysisStatus.PENDING từ schema
    )
    db.add(db_request)
    db.commit()
    db.refresh(db_request)
    return db_request

def get_pr_analysis_request_by_id(db: Session, request_id: int) -> PRAnalysisRequest | None:
    """
    Lấy một PRAnalysisRequest bằng ID.
    """
    return db.query(PRAnalysisRequest).filter(PRAnalysisRequest.id == request_id).first()

def update_pr_analysis_request_status(
    db: Session, 
    request_id: int, 
    status: PRAnalysisStatus, 
    error_message: str | None = None
) -> PRAnalysisRequest | None:
    """
    Cập nhật status của một PRAnalysisRequest.
    """
    db_request = get_pr_analysis_request_by_id(db, request_id)
    if db_request:
        db_request.status = status
        if status == PRAnalysisStatus.PROCESSING:
            db_request.started_at = func.now() # Hoặc datetime.now(timezone.utc)
        elif status in [PRAnalysisStatus.COMPLETED, PRAnalysisStatus.FAILED]:
            db_request.completed_at = func.now() # Hoặc datetime.now(timezone.utc)
        
        if error_message:
            db_request.error_message = error_message
        
        db.commit()
        db.refresh(db_request)
    return db_request

def get_pr_analysis_requests_by_project_id(
    db: Session, project_id: int, skip: int = 0, limit: int = 20
) -> List[PRAnalysisRequest]:
    """
    Lấy danh sách các PRAnalysisRequest cho một project_id, sắp xếp theo ngày yêu cầu giảm dần.
    """
    return (
        db.query(PRAnalysisRequest)
        .filter(PRAnalysisRequest.project_id == project_id)
        .order_by(PRAnalysisRequest.requested_at.desc()) # Sắp xếp theo requested_at giảm dần
        .offset(skip)
        .limit(limit)
        .all()
    )

def count_pr_analysis_requests_by_project_id(db: Session, project_id: int) -> int:
    """
    Đếm tổng số PRAnalysisRequest cho một project_id.
    """
    count_query = (
        db.query(func.count(PRAnalysisRequest.id)) # Sử dụng func.count
        .filter(PRAnalysisRequest.project_id == project_id)
    )
    result = count_query.scalar()
    return result if result is not None else 0