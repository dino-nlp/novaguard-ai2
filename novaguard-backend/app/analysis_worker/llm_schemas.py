# novaguard-backend/app/analysis_worker/llm_schemas.py
from pydantic import BaseModel, Field
from typing import List, Optional

class LLMSingleFinding(BaseModel):
    """
    Represents a single issue/finding identified by the LLM.
    Corresponds to the fields requested from the LLM in the prompt.
    """
    file_path: str = Field(description="The full path of the relevant file where the issue is found.")
    line_start: Optional[int] = Field(None, description="The starting line number (1-based) of the relevant code segment.")
    line_end: Optional[int] = Field(None, description="The ending line number (1-based) of the relevant code segment. If the issue is on a single line, this can be the same as line_start or omitted.")
    severity: str = Field(description="Severity of the issue. Must be one of: 'Error', 'Warning', or 'Note'.")
    message: str = Field(description="A clear, concise, and detailed description of the identified issue.")
    suggestion: Optional[str] = Field(None, description="A concrete suggestion on how to fix or improve the code related to this issue.")

class LLMStructuredOutput(BaseModel):
    """
    The overall JSON structure expected from the LLM.
    It contains a list of all findings.
    """
    findings: List[LLMSingleFinding] = Field(description="A list of all distinct issues identified in the provided code. If no issues are found, this should be an empty list.")