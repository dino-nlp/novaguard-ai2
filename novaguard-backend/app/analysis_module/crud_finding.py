from sqlalchemy.orm import Session
from typing import List

from app.models import AnalysisFinding, PyAnalysisSeverity # Import từ app.models
from .schemas_finding import AnalysisFindingCreate # Import từ cùng module

def create_analysis_findings(
    db: Session,
    pr_analysis_request_id: int,
    findings_in: List[AnalysisFindingCreate] # findings_in[i].severity là SeverityLevel
) -> List[AnalysisFinding]:
    db_findings = []
    if not findings_in:
        return db_findings

    for finding_in in findings_in:
        # Chuyển đổi từ Pydantic Enum (SeverityLevel) sang SQLAlchemy Enum (PyAnalysisSeverity)
        # nếu chúng khác nhau. Nếu chúng giống hệt nhau (cùng tên member và value),
        # SQLAlchemy có thể tự xử lý. Tuy nhiên, để chắc chắn:
        db_severity_value: PyAnalysisSeverity
        try:
            # finding_in.severity.value là string ('Error', 'Warning', etc.)
            db_severity_value = PyAnalysisSeverity(finding_in.severity.value)
        except ValueError:
            # Xử lý trường hợp giá trị không hợp lệ nếu cần, mặc dù Pydantic validator nên bắt rồi
            # logger.warning(f"Invalid severity '{finding_in.severity.value}' for DB. Defaulting or skipping.")
            # continue # Hoặc gán giá trị mặc định
            # Vì LLMSingleFinding đã validate, bước này chủ yếu là để đảm bảo kiểu đúng
            # nếu PyAnalysisSeverity và SeverityLevel là hai enum class khác nhau.
            # Nếu chúng là cùng một class (ví dụ, cả hai import từ một nguồn chung),
            # thì chỉ cần gán: db_severity_value = finding_in.severity
            # Nhưng hiện tại, LLMSingleFinding dùng SeverityLevel, AnalysisFinding dùng PyAnalysisSeverity.
            # Chúng có cùng giá trị string, nên việc chuyển đổi qua value là an toàn.
            pass # Giả sử validator của Pydantic đã đảm bảo giá trị hợp lệ.

        db_finding = AnalysisFinding(
            pr_analysis_request_id=pr_analysis_request_id,
            file_path=finding_in.file_path,
            line_start=finding_in.line_start,
            line_end=finding_in.line_end,
            # severity=finding_in.severity, # TRUYỀN TRỰC TIẾP PYTHON ENUM MEMBER
                                        # SQLAlchemy sẽ lấy .value khi lưu vào DB
            severity=PyAnalysisSeverity(finding_in.severity.value), # Đảm bảo truyền đúng type PyAnalysisSeverity
            message=finding_in.message,
            suggestion=finding_in.suggestion,
            agent_name=finding_in.agent_name,
            code_snippet=finding_in.code_snippet
        )
        db_findings.append(db_finding)

    db.add_all(db_findings)
    db.commit()
    for finding in db_findings:
        db.refresh(finding)
    return db_findings

def get_findings_by_request_id(db: Session, pr_analysis_request_id: int) -> List[AnalysisFinding]:
    return db.query(AnalysisFinding).filter(AnalysisFinding.pr_analysis_request_id == pr_analysis_request_id).all()