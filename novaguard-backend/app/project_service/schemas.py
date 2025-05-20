from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Literal, Union
from datetime import datetime
from pydantic import BaseModel as PydanticBaseModel, HttpUrl
from app.models.full_project_analysis_request_model import FullProjectAnalysisStatus # Import enum
from app.models.pr_analysis_request_model import PRAnalysisStatus
from app.models.project_model import LLMProviderEnum, OutputLanguageEnum


def empty_str_to_none(value: Optional[str]) -> Optional[str]:
    if value == "":
        return None
    return value

# Schema cơ bản cho Project
class ProjectBase(BaseModel):
    # user_id: int # Sẽ được lấy từ current_user, không cần user nhập
    github_repo_id: str = Field(..., description="GitHub Repository ID (numerical string or full name like 'owner/repo')")
    repo_name: str = Field(..., min_length=1, description="Display name for the project, can be the full GitHub repo name")
    main_branch: str = Field(default="main", description="Main branch to track (e.g., main, master)")
    language: Optional[str] = Field(None, max_length=100, description="Primary programming language of the project")
    custom_project_notes: Optional[str] = Field(None, description="Custom notes about project architecture or coding conventions")
    
    llm_provider: Optional[LLMProviderEnum] = Field(default=LLMProviderEnum.OLLAMA)
    llm_model_name: Optional[str] = Field(None, description="Specific LLM model name. Leave empty/blank to use provider's default.") # Cho phép để trống
    llm_temperature: Optional[float] = Field(default=0.1, ge=0.0, le=2.0, description="LLM temperature (0.0 to 2.0)")
    # llm_api_key_override sẽ được xử lý riêng ở ProjectCreate / ProjectUpdate để không expose key đã mã hóa
    output_language: Optional[OutputLanguageEnum] = Field(default=OutputLanguageEnum.ENGLISH, description="Language for analysis results (en, vi, ko)")

    # Validator cho các trường optional string để chuyển "" thành None
    _normalize_language = field_validator('language', 'llm_model_name', 'custom_project_notes', mode='before')(empty_str_to_none)
    _normalize_llm_model_name = field_validator('llm_model_name', mode='before')(empty_str_to_none) 
    
    class Config: # Đây là Config của ProjectBase
        # from_attributes = True
        # use_enum_values = True
        pass

# Schema cho việc tạo Project (input từ API)
class ProjectCreate(ProjectBase):
    # Cho phép truyền API key override khi tạo, nó sẽ được mã hóa trong CRUD
    llm_api_key_override: Optional[str] = Field(None, description="API Key to override global settings (will be encrypted)")
    _normalize_api_key = field_validator('llm_api_key_override', mode='before')(empty_str_to_none)

class GitHubRepoSchema(PydanticBaseModel):
    id: int
    name: str
    full_name: str # owner/repo
    private: bool
    html_url: HttpUrl
    description: Optional[str] = None
    updated_at: datetime
    default_branch: Optional[str] = None # << THÊM DÒNG NÀY

    class Config: # Thêm Config nếu bạn muốn dùng from_attributes hoặc các cấu hình Pydantic khác
        from_attributes = True
        

# Schema cho việc cập nhật Project (input từ API - cho phép cập nhật một số trường)
class ProjectUpdate(BaseModel):
    repo_name: Optional[str] = Field(None, min_length=1)
    main_branch: Optional[str] = Field(None)
    language: Optional[str] = Field(None, max_length=100)
    custom_project_notes: Optional[str] = Field(None)
    
    llm_provider: Optional[LLMProviderEnum] = Field(None) # None nghĩa là không thay đổi
    llm_model_name: Optional[str] = Field(None) # Truyền "" để xóa, None để không đổi
    llm_temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    llm_api_key_override: Optional[str] = Field(None, description="New API Key to override (will be encrypted). Send empty string to clear.")
    output_language: Optional[OutputLanguageEnum] = Field(None)

    # Validator cho các trường optional string để chuyển "" thành None
    _normalize_language_update = field_validator('language', 'custom_project_notes', mode='before')(empty_str_to_none)
    # llm_model_name và llm_api_key_override: Nếu user muốn xóa, họ sẽ truyền chuỗi rỗng.
    # Chúng ta sẽ xử lý việc này trong CRUD (chuỗi rỗng -> xóa/None, None -> không thay đổi)


# Schema cho việc hiển thị thông tin Project (output cho API)
class ProjectPublic(ProjectBase): # Kế thừa từ ProjectBase để có các trường mới
    id: int
    user_id: int
    github_webhook_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    
    # Hiển thị llm_api_key_override_is_set thay vì key thật
    llm_api_key_override_is_set: bool = Field(False, description="True if a custom API key override is set for this project")

    class Config:
        from_attributes = True
        # use_enum_values = True # Để khi serialize, giá trị của Enum được dùng (ví dụ: "ollama")


# Schema để trả về danh sách Project
class ProjectList(BaseModel):
    projects: list[ProjectPublic]
    total: int
    
class FullProjectAnalysisRequestBase(BaseModel):
    project_id: int
    branch_name: str

class FullProjectAnalysisRequestCreate(BaseModel): # Input cho API trigger
    pass # project_id sẽ lấy từ path, branch_name từ project settings

class FullProjectAnalysisRequestPublic(FullProjectAnalysisRequestBase):
    id: int
    status: FullProjectAnalysisStatus
    error_message: Optional[str] = None
    requested_at: datetime
    started_at: Optional[datetime] = None
    source_fetched_at: Optional[datetime] = None
    ckg_built_at: Optional[datetime] = None
    analysis_completed_at: Optional[datetime] = None
    total_files_analyzed: Optional[int] = None
    total_findings: Optional[int] = None

    class Config:
        from_attributes = True
        use_enum_values = True # Để serialize enum thành string

class FullProjectAnalysisRequestList(BaseModel):
    items: List[FullProjectAnalysisRequestPublic]
    total: int
    
class AnalysisHistoryItem(BaseModel):
    id: int
    scan_type: Literal["pr", "full"]
    identifier: Optional[str] = None # PR number cho PR scan, hoặc Branch cho Full scan
    title: Optional[str] = None # PR Title cho PR scan
    status: Union[PRAnalysisStatus, FullProjectAnalysisStatus] # Sử dụng Union để chấp nhận cả hai loại Enum
    requested_at: datetime
    report_url: Optional[HttpUrl] = None # URL đến trang report chi tiết
    total_errors: Optional[int] = 0
    total_warnings: Optional[int] = 0
    total_other_findings: Optional[int] = 0

    class Config:
        from_attributes = True
        use_enum_values = True