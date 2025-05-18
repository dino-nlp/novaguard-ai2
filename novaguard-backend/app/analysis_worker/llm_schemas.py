# novaguard-backend/app/analysis_worker/llm_schemas.py
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Union
import enum

import logging
logger = logging.getLogger(__name__)

class SeverityLevel(str, enum.Enum): # Kế thừa từ str và enum.Enum
    ERROR = "Error"
    WARNING = "Warning"
    NOTE = "Note"
    INFO = "Info"
    
class LLMSingleFinding(BaseModel):
    """
    Represents a single issue/finding identified by the LLM, typically file-specific.
    """
    file_path: Optional[str] = Field(None, description="The full path of the relevant file where the issue is found. Can be null for project-level findings.")
    line_start: Optional[int] = Field(None, description="The starting line number (1-based). Null if not applicable.")
    line_end: Optional[int] = Field(None, description="The ending line number (1-based). Null if not applicable.")
    severity: SeverityLevel = Field(description="Severity of the issue.")
    message: str = Field(description="A clear, concise, and detailed description of the identified issue.")
    suggestion: Optional[str] = Field(None, description="A concrete suggestion on how to fix or improve the code related to this issue.")
    finding_type: Optional[str] = Field("code_smell", description="Type of finding, e.g., 'code_smell', 'security_vuln', 'performance_bottleneck', 'architectural_issue'.")
    # Thêm trường `meta_data` để chứa thông tin bổ sung, ví dụ tên class/function nếu có
    meta_data: Optional[dict] = Field(None, description="Additional structured data about the finding, e.g., {'class_name': 'MyClass', 'offending_symbol': 'some_variable'}")


    @field_validator('severity', mode='before')
    @classmethod
    def _validate_severity(cls, value: str) -> SeverityLevel:
        try:
            return SeverityLevel(value.capitalize() if isinstance(value, str) else value)
        except ValueError:
            # Default to NOTE if severity is not recognized
            logger.warning(f"Invalid severity value '{value}' from LLM. Defaulting to Note.")
            return SeverityLevel.NOTE
            # raise ValueError(f"Invalid severity value: '{value}'. Must be one of {', '.join([e.value for e in SeverityLevel])}")

class LLMProjectLevelFinding(BaseModel):
    """
    Represents a single issue/finding identified at the project or module level.
    """
    finding_category: str = Field(description="Category of the project-level finding, e.g., 'Architectural Concern', 'Technical Debt', 'Security Hotspot', 'Module Design'.")
    description: str = Field(description="Detailed description of the project-level issue.")
    severity: SeverityLevel = Field(description="Severity of the issue.")
    implication: Optional[str] = Field(None, description="Potential impact of this issue on the project.")
    recommendation: Optional[str] = Field(None, description="High-level recommendation to address the issue.")
    # relevant_modules or relevant_components có thể là list các string (tên file/module)
    relevant_components: Optional[List[str]] = Field(None, description="List of key files, modules, or components related to this finding.")
    meta_data: Optional[dict] = Field(None, description="Additional structured data about the project-level finding.")

class LLMStructuredOutput(BaseModel):
    """
    The overall JSON structure expected from the LLM for PR analysis or file-specific analysis.
    """
    findings: List[LLMSingleFinding] = Field(description="A list of all distinct issues identified. If no issues are found, this should be an empty list.")

class LLMProjectAnalysisOutput(BaseModel):
    """
    The overall JSON structure expected from the LLM for full project analysis.
    May contain a mix of project-level and more granular findings.
    """
    project_summary: Optional[str] = Field(None, description="A brief overall summary of the project's health or key observations.")
    project_level_findings: List[LLMProjectLevelFinding] = Field(default_factory=list, description="List of findings that apply at a project or architectural level.")
    # Optionally, can also include more granular findings if the agent identifies them during full scan
    granular_findings: List[LLMSingleFinding] = Field(default_factory=list, description="List of specific code-level findings identified during the full project scan.")
