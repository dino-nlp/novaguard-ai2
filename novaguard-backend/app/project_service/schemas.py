from pydantic import BaseModel, Field
from typing import Optional, List, Literal, Union
from datetime import datetime
from pydantic import BaseModel as PydanticBaseModel, HttpUrl 
from app.models.full_project_analysis_request_model import FullProjectAnalysisStatus # Import enum
from app.models.pr_analysis_request_model import PRAnalysisStatus

# Schema cơ bản cho Project
class ProjectBase(BaseModel):
    # user_id: int # Sẽ được lấy từ current_user, không cần user nhập
    github_repo_id: str = Field(..., description="GitHub Repository ID (numerical string or full name like 'owner/repo')")
    repo_name: str = Field(..., min_length=1, description="Display name for the project, can be the full GitHub repo name")
    main_branch: str = Field(default="main", description="Main branch to track (e.g., main, master)")
    language: Optional[str] = Field(None, max_length=100, description="Primary programming language of the project")
    custom_project_notes: Optional[str] = Field(None, description="Custom notes about project architecture or coding conventions")

# Schema cho việc tạo Project (input từ API)
class ProjectCreate(ProjectBase):
    pass # Kế thừa tất cả từ ProjectBase

# --- Schema cho GitHub Repo (để trả về cho frontend) ---
# Định nghĩa schema này ở đây vì nó được sử dụng và trả về bởi API này.
# Hoặc có thể đặt trong project_schemas_module nếu muốn.
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

# Schema cho việc hiển thị thông tin Project (output cho API)
class ProjectPublic(ProjectBase):
    id: int
    user_id: int # Hiển thị user_id của chủ sở hữu
    github_webhook_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

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