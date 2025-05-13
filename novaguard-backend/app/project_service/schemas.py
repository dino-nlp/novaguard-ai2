from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

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