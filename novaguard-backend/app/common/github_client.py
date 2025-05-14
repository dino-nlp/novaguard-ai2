# novaguard-backend/app/common/github_client.py
import httpx
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

GITHUB_API_BASE_URL = "https://api.github.com"
GITHUB_API_VERSION_HEADER = {"X-GitHub-Api-Version": "2022-11-28"}

class GitHubAPIClient:
    def __init__(self, token: str):
        if not token:
            raise ValueError("GitHub token is required for APIClient.")
        self.token = token
        # Headers mặc định cho các request JSON
        self.default_json_headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json",
            **GITHUB_API_VERSION_HEADER
        }
        # Headers riêng cho việc lấy diff
        self.diff_headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3.diff",
            **GITHUB_API_VERSION_HEADER
        }

    async def _request(self, method: str, url: str, custom_headers: Optional[Dict[str, str]] = None, **kwargs) -> httpx.Response:
        """
        Helper function to make HTTP requests to the GitHub API.
        Uses custom_headers if provided, otherwise defaults to self.default_json_headers.
        Other kwargs are passed directly to httpx.AsyncClient.request().
        """
        async with httpx.AsyncClient() as client:
            # Xác định headers sẽ sử dụng
            headers_to_use = custom_headers if custom_headers is not None else self.default_json_headers
            
            # Ghi log headers sẽ được sử dụng (có thể bỏ qua Authorization token để tránh lộ)
            loggable_headers = {k: v for k, v in headers_to_use.items() if k.lower() != 'authorization'}
            logger.debug(f"GitHub API Request: {method} {url} with headers: {loggable_headers}, other_kwargs: {kwargs}")
            
            try:
                # Không truyền `headers` trong **kwargs nữa, chỉ truyền `custom_headers` (đã được gộp vào `headers_to_use`)
                response = await client.request(method, url, headers=headers_to_use, **kwargs)
                response.raise_for_status()  # Raise HTTPStatusError cho 4xx/5xx
                return response
            except httpx.HTTPStatusError as e:
                # Ghi log chi tiết hơn về lỗi từ GitHub
                error_details = e.response.text[:500] # Lấy 500 ký tự đầu của response lỗi
                try:
                    json_error = e.response.json()
                    error_details = json_error.get("message", error_details)
                    if "errors" in json_error:
                        error_details += f" Details: {json_error['errors']}"
                except ValueError: # Nếu response không phải JSON
                    pass
                logger.error(
                    f"GitHub API Error: {e.response.status_code} - {e.request.url} - Response: {error_details}"
                )
                raise # Re-throw để hàm gọi bên ngoài có thể xử lý hoặc trả về None
            except httpx.RequestError as e: # Lỗi kết nối, timeout, DNS, etc.
                logger.error(f"GitHub API Request Error (e.g., connection, timeout): {e.request.url} - {str(e)}")
                raise
            except Exception as e: # Các lỗi không mong muốn khác
                logger.exception(f"Unexpected error during GitHub API request to {url}")
                raise

    async def get_pull_request_details(self, owner: str, repo: str, pr_number: int) -> Optional[Dict[str, Any]]:
        """Lấy thông tin chi tiết của một Pull Request."""
        url = f"{GITHUB_API_BASE_URL}/repos/{owner}/{repo}/pulls/{pr_number}"
        try:
            # Sử dụng headers mặc định (default_json_headers)
            response = await self._request("GET", url) 
            return response.json()
        except Exception:
            # _request đã log lỗi, hàm này chỉ trả về None để báo hiệu thất bại
            return None

    async def get_pull_request_diff(self, owner: str, repo: str, pr_number: int) -> Optional[str]:
        """Lấy nội dung diff của một Pull Request."""
        url = f"{GITHUB_API_BASE_URL}/repos/{owner}/{repo}/pulls/{pr_number}"
        try:
            # Truyền self.diff_headers làm custom_headers
            response = await self._request("GET", url, custom_headers=self.diff_headers)
            return response.text
        except Exception:
            return None

    async def get_pull_request_files(self, owner: str, repo: str, pr_number: int) -> Optional[List[Dict[str, Any]]]:
        """Lấy danh sách các file thay đổi trong một Pull Request."""
        # URL ban đầu không có &page=1, để _request tự xử lý params nếu có
        base_url = f"{GITHUB_API_BASE_URL}/repos/{owner}/{repo}/pulls/{pr_number}/files"
        all_files = []
        page = 1
        params = {"per_page": 100, "page": page} # Bắt đầu với page 1

        while True:
            try:
                # logger.debug(f"Fetching PR files page {page} with params: {params}")
                # Sử dụng headers mặc định, truyền params cho request
                response = await self._request("GET", base_url, params=params)
                files_page = response.json()
                
                if not files_page: # Không có file nào nữa hoặc trang trống
                    break
                all_files.extend(files_page)
                
                # Kiểm tra Link header để phân trang
                link_header = response.headers.get("Link")
                if link_header and 'rel="next"' in link_header:
                    page += 1
                    params["page"] = page # Cập nhật page cho lần lặp tiếp theo
                else: # Không có trang tiếp theo
                    break
            except Exception as e:
                logger.error(f"Error fetching page {page} of PR files: {e}")
                # Trả về những gì đã lấy được nếu có lỗi giữa chừng, hoặc None nếu lỗi ngay từ đầu
                return all_files if all_files else None
        return all_files

    async def create_pr_comment(
        self, owner: str, repo: str, pr_number: int, body: str
    ) -> Optional[Dict[str, Any]]:
        """
        Tạo một comment trên một Pull Request (thông qua API Issues).
        pr_number ở đây là issue_number.
        """
        # PRs là Issues, nên chúng ta dùng issue_number (chính là pr_number)
        url = f"{GITHUB_API_BASE_URL}/repos/{owner}/{repo}/issues/{pr_number}/comments"
        payload = {"body": body}
        logger.info(f"Attempting to create comment on PR {owner}/{repo}#{pr_number}")
        try:
            response = await self._request("POST", url, json=payload) # Sử dụng default_json_headers
            return response.json()
        except Exception as e:
            logger.error(f"Failed to create comment on PR {owner}/{repo}#{pr_number}: {e}")
            return None

    async def get_file_content(self, owner: str, repo: str, file_path: str, ref: Optional[str] = None) -> Optional[str]:
        """
        Lấy nội dung của một file cụ thể từ repository tại một ref (commit SHA, branch, tag).
        """
        url = f"{GITHUB_API_BASE_URL}/repos/{owner}/{repo}/contents/{file_path}"
        params_for_request = {} # Đổi tên để tránh nhầm với biến params của _request
        if ref:
            params_for_request["ref"] = ref
        
        try:
            # Sử dụng headers mặc định, truyền params_for_request
            response = await self._request("GET", url, params=params_for_request)
            data = response.json()
            if data.get("encoding") == "base64" and data.get("content"):
                import base64
                try:
                    return base64.b64decode(data["content"]).decode('utf-8')
                except UnicodeDecodeError:
                    logger.warning(f"Could not decode base64 content as UTF-8 for {file_path} at ref {ref}. It might be a binary file.")
                    return "[Binary Content - Not Decoded]"
            elif data.get("content"):
                return data.get("content")
            elif data.get("download_url"):
                logger.info(f"File {file_path} (ref: {ref}) has download_url, fetching content from there.")
                # Khi gọi download_url, không cần truyền headers mặc định của GitHub API (vì nó có thể là S3 URL chẳng hạn)
                # và cũng không cần Authorization token nếu URL đã signed.
                # Tuy nhiên, để đơn giản, chúng ta vẫn có thể dùng _request với custom_headers rỗng hoặc headers mặc định
                # nếu download_url vẫn là của GitHub.
                # Nếu download_url là một URL công khai, không cần token.
                # Với trường hợp này, ta vẫn dùng _request nhưng có thể truyền custom_headers=None để nó không dùng default_json_headers,
                # hoặc một bộ headers tối thiểu.
                # Tuy nhiên, nếu download_url là của githubusercontent.com, nó thường không cần token.
                #
                # Cách an toàn: tạo một client httpx mới cho download_url nếu nó không phải domain GitHub API
                if "api.github.com" not in data["download_url"]:
                    async with httpx.AsyncClient() as direct_client:
                        download_response = await direct_client.get(data["download_url"], timeout=30.0)
                        download_response.raise_for_status()
                        return download_response.text
                else: # Nếu vẫn là domain api.github.com (ít khả năng cho download_url)
                    download_response = await self._request("GET", data["download_url"]) # Sẽ dùng default headers
                    return download_response.text
            return None
        except Exception:
            return None