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
        self.headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json",
            **GITHUB_API_VERSION_HEADER
        }
        self.diff_headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3.diff", # Yêu cầu định dạng diff
            **GITHUB_API_VERSION_HEADER
        }

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        async with httpx.AsyncClient() as client:
            try:
                logger.debug(f"GitHub API Request: {method} {url} with headers {kwargs.get('headers', self.headers)}")
                response = await client.request(method, url, headers=kwargs.get('headers', self.headers), **kwargs)
                response.raise_for_status()  # Raise HTTPStatusError cho 4xx/5xx
                return response
            except httpx.HTTPStatusError as e:
                logger.error(f"GitHub API Error: {e.response.status_code} - {e.request.url} - Response: {e.response.text}")
                # Có thể throw một custom exception ở đây để xử lý cụ thể hơn ở nơi gọi
                raise
            except httpx.RequestError as e:
                logger.error(f"GitHub API Request Error: {e.request.url} - {e}")
                raise
            except Exception as e:
                logger.exception(f"Unexpected error during GitHub API request to {url}")
                raise


    async def get_pull_request_details(self, owner: str, repo: str, pr_number: int) -> Optional[Dict[str, Any]]:
        """Lấy thông tin chi tiết của một Pull Request."""
        url = f"{GITHUB_API_BASE_URL}/repos/{owner}/{repo}/pulls/{pr_number}"
        try:
            response = await self._request("GET", url)
            return response.json()
        except Exception:
            return None

    async def get_pull_request_diff(self, owner: str, repo: str, pr_number: int) -> Optional[str]:
        """Lấy nội dung diff của một Pull Request."""
        # Có thể lấy từ diff_url của PR details, hoặc gọi API này
        url = f"{GITHUB_API_BASE_URL}/repos/{owner}/{repo}/pulls/{pr_number}"
        try:
            response = await self._request("GET", url, headers=self.diff_headers)
            return response.text # Diff thường là text
        except Exception:
            return None

    async def get_pull_request_files(self, owner: str, repo: str, pr_number: int) -> Optional[List[Dict[str, Any]]]:
        """Lấy danh sách các file thay đổi trong một Pull Request."""
        url = f"{GITHUB_API_BASE_URL}/repos/{owner}/{repo}/pulls/{pr_number}/files?per_page=100" # Tối đa 100 file/page
        all_files = []
        page = 1
        while True:
            paginated_url = f"{url}&page={page}"
            try:
                response = await self._request("GET", paginated_url)
                files_page = response.json()
                if not files_page: # Không có file nào nữa hoặc trang trống
                    break
                all_files.extend(files_page)
                if len(files_page) < 100: # Đã lấy hết file (GitHub trả về ít hơn per_page)
                    break
                page += 1
            except Exception:
                return None # Hoặc trả về những gì đã lấy được: return all_files if all_files else None
        return all_files


    async def get_file_content(self, owner: str, repo: str, file_path: str, ref: Optional[str] = None) -> Optional[str]:
        """
        Lấy nội dung của một file cụ thể từ repository tại một ref (commit SHA, branch, tag).
        Nội dung trả về là base64 encoded nếu file không phải text.
        Hàm này sẽ cố gắng decode nếu là text.
        """
        url = f"{GITHUB_API_BASE_URL}/repos/{owner}/{repo}/contents/{file_path}"
        params = {}
        if ref:
            params["ref"] = ref
        
        try:
            response = await self._request("GET", url, params=params)
            data = response.json()
            if data.get("encoding") == "base64" and data.get("content"):
                import base64
                try:
                    # Cố gắng decode UTF-8, nếu lỗi thì có thể là file binary
                    return base64.b64decode(data["content"]).decode('utf-8')
                except UnicodeDecodeError:
                    logger.warning(f"Could not decode base64 content as UTF-8 for {file_path}. It might be a binary file.")
                    return "[Binary Content - Not Decoded]" # Hoặc trả về None/data["content"] (base64 string)
            elif data.get("content"): # Trường hợp không phải base64 (hiếm gặp cho file content)
                 return data.get("content")
            elif data.get("download_url"): # Nếu là file lớn, có thể cần fetch download_url
                logger.info(f"File {file_path} has download_url, fetching content from there.")
                download_response = await self._request("GET", data["download_url"])
                return download_response.text # Thường là raw content
            return None # Không có content hoặc encoding không hỗ trợ
        except Exception:
            return None

# Ví dụ sử dụng (sẽ được gọi từ worker)
# async def main_example():
#     # Cần token thật và repo/pr thật để test
#     # token = "your_github_token"
#     # if not token: return
#     # client = GitHubAPIClient(token=token)
#     # owner, repo, pr_number = "octocat", "Spoon-Knife", 1 
#     # details = await client.get_pull_request_details(owner, repo, pr_number)
#     # if details: print(f"PR Details: {details.get('title')}")
#     # files = await client.get_pull_request_files(owner, repo, pr_number)
#     # if files:
#     #     print(f"Files changed: {len(files)}")
#     #     for f in files:
#     #         print(f" - {f['filename']} (Status: {f['status']})")
#     #         if f['status'] != 'removed':
#     #             content = await client.get_file_content(owner, repo, f['filename'], ref=details['head']['sha'])
#     #             print(f"   Content (first 100 chars): {content[:100] if content else 'N/A'}")
#     # diff = await client.get_pull_request_diff(owner, repo, pr_number)
#     # if diff: print(f"PR Diff (first 200 chars): {diff[:200]}")

# if __name__ == "__main__":
#     import asyncio
#     # asyncio.run(main_example())