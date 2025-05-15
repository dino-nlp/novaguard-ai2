# novaguard-backend/app/analysis_worker/llm_schemas.py
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional
import enum


class SeverityLevel(str, enum.Enum): # Kế thừa từ str và enum.Enum
    ERROR = "Error"
    WARNING = "Warning"
    NOTE = "Note"
    INFO = "Info"
    
class LLMSingleFinding(BaseModel):
    """
    Represents a single issue/finding identified by the LLM.
    Corresponds to the fields requested from the LLM in the prompt.
    """
    file_path: str = Field(description="The full path of the relevant file where the issue is found.")
    line_start: Optional[int] = Field(None, description="The starting line number (1-based) of the relevant code segment.")
    line_end: Optional[int] = Field(None, description="The ending line number (1-based) of the relevant code segment. If the issue is on a single line, this can be the same as line_start or omitted.")
    severity: SeverityLevel = Field(description="Severity of the issue.")
    message: str = Field(description="A clear, concise, and detailed description of the identified issue.")
    suggestion: Optional[str] = Field(None, description="A concrete suggestion on how to fix or improve the code related to this issue.")
    
    @field_validator('severity', mode='before')
    @classmethod
    def _validate_severity(cls, value: str) -> SeverityLevel:
        try:
            # Cố gắng chuyển đổi giá trị đầu vào (string từ LLM) thành enum member
            # Điều này giúp linh hoạt với chữ hoa/thường từ LLM nếu cần
            return SeverityLevel(value.capitalize() if isinstance(value, str) else value)
        except ValueError:
            # Nếu không khớp, có thể log hoặc raise lỗi, hoặc trả về một giá trị mặc định
            # Ở đây, chúng ta cho phép Pydantic raise lỗi nếu không hợp lệ
            # Hoặc bạn có thể trả về một giá trị mặc định:
            # print(f"Warning: Invalid severity value '{value}' from LLM. Defaulting to Note.")
            # return SeverityLevel.NOTE
            raise ValueError(f"Invalid severity value: '{value}'. Must be one of {', '.join([e.value for e in SeverityLevel])}")


class LLMStructuredOutput(BaseModel):
    """
    The overall JSON structure expected from the LLM.
    It contains a list of all findings.
    """
    findings: List[LLMSingleFinding] = Field(description="A list of all distinct issues identified in the provided code. If no issues are found, this should be an empty list.")