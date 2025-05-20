# novaguard-backend/app/project_service/crud_full_scan.py
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timezone

from app.models import FullProjectAnalysisRequest, FullProjectAnalysisStatus, Project
from sqlalchemy import func
import logging
logger = logging.getLogger(__name__)

def create_full_scan_request(
    db: Session, project_id: int, branch_name: str
) -> FullProjectAnalysisRequest:
    db_request = FullProjectAnalysisRequest(
        project_id=project_id,
        branch_name=branch_name,
        status=FullProjectAnalysisStatus.PENDING
    )
    db.add(db_request)
    db.commit()
    db.refresh(db_request)
    return db_request

def get_full_scan_request_by_id(db: Session, request_id: int) -> Optional[FullProjectAnalysisRequest]:
    return db.query(FullProjectAnalysisRequest).filter(FullProjectAnalysisRequest.id == request_id).first()

def update_full_scan_request_status(
    db: Session,
    request_id: int,
    new_status: FullProjectAnalysisStatus,
    error_message: Optional[str] = None
) -> Optional[FullProjectAnalysisRequest]:
    db_request = get_full_scan_request_by_id(db, request_id)
    if db_request:
        db_request.status = new_status
        if new_status == FullProjectAnalysisStatus.PROCESSING and not db_request.started_at:
            db_request.started_at = datetime.now(timezone.utc)
        elif new_status == FullProjectAnalysisStatus.SOURCE_FETCHED:
            db_request.source_fetched_at = datetime.now(timezone.utc)
        elif new_status == FullProjectAnalysisStatus.CKG_BUILDING:
            pass # Thời gian CKG built sẽ được cập nhật riêng khi hoàn thành
        elif new_status == FullProjectAnalysisStatus.COMPLETED:
            db_request.analysis_completed_at = datetime.now(timezone.utc)
        elif new_status == FullProjectAnalysisStatus.FAILED:
            if not db_request.analysis_completed_at: # Chỉ set nếu chưa từng complete
                db_request.analysis_completed_at = datetime.now(timezone.utc)

        if error_message:
            db_request.error_message = error_message
        else: # Xóa error message nếu status không phải FAILED
            if new_status != FullProjectAnalysisStatus.FAILED:
                db_request.error_message = None

        try:
            db.commit()
            db.refresh(db_request)
        except Exception as e:
            db.rollback()
            logger.error(f"Error committing FullProjectAnalysisRequest (ID: {request_id}) status '{new_status.value}': {e}", exc_info=True)
            return None

        # Cập nhật project liên quan
        project = db.query(Project).filter(Project.id == db_request.project_id).first()
        if project:
            project.last_full_scan_request_id = db_request.id
            project.last_full_scan_status = new_status
            if new_status == FullProjectAnalysisStatus.COMPLETED:
                project.last_full_scan_at = db_request.analysis_completed_at
            elif new_status == FullProjectAnalysisStatus.FAILED: # Nếu FAILED, cũng cập nhật last_full_scan_at
                project.last_full_scan_at = db_request.analysis_completed_at if db_request.analysis_completed_at else datetime.now(timezone.utc)

            try:
                db.commit() # Commit thay đổi cho project
            except Exception as e_project_commit: # Đổi tên biến lỗi
                db.rollback()
                # Log lỗi chi tiết hơn
                logger.error(
                    f"Error committing Project (ID: {project.id}) update linked to Full Scan (ID: {request_id}) with status '{new_status.value}'. Error: {e_project_commit}",
                    exc_info=True
                )
                
    return db_request

def get_full_scan_requests_for_project(
    db: Session, project_id: int, skip: int = 0, limit: int = 10
) -> List[FullProjectAnalysisRequest]:
    return (
        db.query(FullProjectAnalysisRequest)
        .filter(FullProjectAnalysisRequest.project_id == project_id)
        .order_by(FullProjectAnalysisRequest.requested_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )

def count_full_scan_requests_for_project(db: Session, project_id: int) -> int:
    return db.query(func.count(FullProjectAnalysisRequest.id)).filter(FullProjectAnalysisRequest.project_id == project_id).scalar() or 0