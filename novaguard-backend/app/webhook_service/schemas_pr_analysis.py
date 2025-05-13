from pydantic import BaseModel, HttpUrl
from typing import Optional, List
from datetime import datetime
from app.models import PRAnalysisStatus # Import Enum status

# Schema cơ bản cho PRAnalysisRequest
class PRAnalysisRequestBase(BaseModel):
    project_id: int # Sẽ được xác định từ webhook
    pr_number: int
    pr_title: Optional[str] = None
    pr_github_url: Optional[HttpUrl] = None # Pydantic sẽ validate URL
    head_sha: Optional[str] = None

# Schema cho việc tạo PRAnalysisRequest (input nội bộ, không phải từ API trực tiếp của user)
class PRAnalysisRequestCreate(PRAnalysisRequestBase):
    status: PRAnalysisStatus = PRAnalysisStatus.PENDING # Mặc định là pending

# Schema cho việc hiển thị thông tin PRAnalysisRequest (output cho API)
class PRAnalysisRequestPublic(PRAnalysisRequestBase):
    id: int
    status: PRAnalysisStatus
    error_message: Optional[str] = None
    requested_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True
        use_enum_values = True # Để khi serialize, giá trị của Enum được dùng (ví dụ: "pending") thay vì Enum member object


# Schema cho payload của GitHub Webhook (chỉ lấy các trường quan trọng cho MVP1)
# Tham khảo: https://docs.github.com/en/webhooks/webhook-events-and-payloads#pull_request
class GitHubUser(BaseModel):
    login: str
    id: int

class GitHubRepo(BaseModel):
    id: int # ID của repository
    name: str # Tên repo
    full_name: str # "owner/repo"

class GitHubPullRequestHead(BaseModel):
    sha: str # head_sha
    # ref: str # tên nhánh (ví dụ: "feature-branch")
    # repo: Optional[GitHubRepo] = None # Thông tin repo của nhánh head (nếu là fork)

class GitHubPullRequestBase(BaseModel):
    ref: str # tên nhánh (ví dụ: "main")
    sha: str
    # repo: GitHubRepo # Thông tin repo của nhánh base

class GitHubPullRequestLinks(BaseModel):
    # html: HttpUrl # URL tới PR trên GitHub
    diff: HttpUrl # URL để lấy diff (ví dụ: "https://github.com/octocat/Hello-World/pull/1.diff")
    # patch: HttpUrl # URL để lấy patch
    # comments: HttpUrl
    # review_comments: HttpUrl
    # commits: HttpUrl
    # statuses: HttpUrl
    # issue: HttpUrl

class GitHubPullRequestData(BaseModel):
    url: HttpUrl # API URL for the PR
    id: int # PR ID (khác pr_number)
    number: int # PR number (hiển thị trên UI GitHub)
    title: Optional[str] = None
    user: GitHubUser # Người tạo PR
    state: str # "open", "closed", "merged"
    # body: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    # closed_at: Optional[datetime] = None
    # merged_at: Optional[datetime] = None
    html_url: HttpUrl # URL tới PR trên GitHub
    diff_url: HttpUrl # URL để lấy file .diff
    head: GitHubPullRequestHead # Thông tin nhánh nguồn (head)
    base: GitHubPullRequestBase # Thông tin nhánh đích (base)
    # _links: GitHubPullRequestLinks # Các URL liên quan, có thể lấy diff_url từ đây hoặc từ trường diff_url chính

class GitHubWebhookPayload(BaseModel):
    action: str # "opened", "reopened", "synchronize", "closed", etc.
    number: Optional[int] = None # PR number, có ở đây hoặc trong pull_request object
    pull_request: Optional[GitHubPullRequestData] = None
    repository: GitHubRepo
    sender: GitHubUser # Người thực hiện action (có thể là bot)
    # installation: Optional[dict] = None # Nếu dùng GitHub App
    
class PRAnalysisRequestItem(BaseModel): # Hoặc kế thừa từ PRAnalysisRequestBase nếu phù hợp
    id: int
    pr_number: int
    pr_title: Optional[str] = None
    status: PRAnalysisStatus 
    requested_at: datetime
    pr_github_url: Optional[HttpUrl] = None # Link tới PR trên GitHub

    class Config:
        from_attributes = True 
        use_enum_values = True