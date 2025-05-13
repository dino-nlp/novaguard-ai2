from sqlalchemy.orm import Session
from typing import List

from app.models import AnalysisFinding # Import từ app.models
from .schemas_finding import AnalysisFindingCreate # Import từ cùng module

def create_analysis_findings(
    db: Session, 
    pr_analysis_request_id: int, 
    findings_in: List[AnalysisFindingCreate]
) -> List[AnalysisFinding]:
    db_findings = []
    if not findings_in: # Không làm gì nếu không có finding nào
        return db_findings

    for finding_in in findings_in:
        db_finding = AnalysisFinding(
            **finding_in.model_dump(),
            pr_analysis_request_id=pr_analysis_request_id
        )
        db_findings.append(db_finding)

    db.add_all(db_findings)
    db.commit()
    # Không cần refresh từng cái vì chúng ta thường không trả về chúng ngay lập tức từ hàm này
    # Nếu cần ID, có thể lấy sau khi commit hoặc SQLAlchemy sẽ tự điền nếu session còn active.
    # For simplicity, returning the list of created objects (they will have IDs after commit if session is active)
    for finding in db_findings: # Để SQLAlchemy load lại ID và các default value
        db.refresh(finding)
    return db_findings

def get_findings_by_request_id(db: Session, pr_analysis_request_id: int) -> List[AnalysisFinding]:
    return db.query(AnalysisFinding).filter(AnalysisFinding.pr_analysis_request_id == pr_analysis_request_id).all()