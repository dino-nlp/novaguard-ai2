from sqlalchemy.orm import Session
from typing import List, Optional
import json

from app.models import AnalysisFinding, PyAnalysisSeverity # Import từ app.models
from .schemas_finding import AnalysisFindingCreate # Import từ cùng module

def create_analysis_findings(
    db: Session,
    findings_in: List[AnalysisFindingCreate],
    pr_analysis_request_id: Optional[int] = None, # Cho phép None
    full_project_analysis_request_id: Optional[int] = None # Thêm mới, cho phép None
) -> List[AnalysisFinding]:
    db_findings = []
    if not findings_in:
        return db_findings

    if not pr_analysis_request_id and not full_project_analysis_request_id:
        raise ValueError("Either pr_analysis_request_id or full_project_analysis_request_id must be provided.")
    if pr_analysis_request_id and full_project_analysis_request_id:
        raise ValueError("Cannot provide both pr_analysis_request_id and full_project_analysis_request_id.")

    for finding_in in findings_in:
        db_severity_value = PyAnalysisSeverity(finding_in.severity.value)

        # Lấy các trường mới từ finding_in nếu có
        finding_level = getattr(finding_in, 'finding_level', 'file') # Mặc định là 'file'
        module_name = getattr(finding_in, 'module_name', None)
        meta_data_dict = getattr(finding_in, 'meta_data', None)
        meta_data_json = json.dumps(meta_data_dict) if meta_data_dict else None


        db_finding = AnalysisFinding(
            pr_analysis_request_id=pr_analysis_request_id,
            full_project_analysis_request_id=full_project_analysis_request_id, # Gán giá trị
            scan_type="pr" if pr_analysis_request_id else "full_project", # Xác định scan_type

            file_path=finding_in.file_path,
            line_start=finding_in.line_start,
            line_end=finding_in.line_end,
            severity=db_severity_value,
            message=finding_in.message,
            suggestion=finding_in.suggestion,
            agent_name=finding_in.agent_name,
            code_snippet=finding_in.code_snippet,

            # Gán các trường mới (cần thêm cột vào model AnalysisFinding và schema.sql)
            finding_type=getattr(finding_in, 'finding_type', None), # Từ LLMSingleFinding
            finding_level=finding_level,
            module_name=module_name,
            meta_data=meta_data_json # Lưu dưới dạng JSON string
        )
        db_findings.append(db_finding)

    db.add_all(db_findings)
    db.commit()
    for finding in db_findings:
        db.refresh(finding)
    return db_findings

def get_findings_by_request_id(db: Session, pr_analysis_request_id: int) -> List[AnalysisFinding]:
    return db.query(AnalysisFinding).filter(AnalysisFinding.pr_analysis_request_id == pr_analysis_request_id).all()