# novaguard-backend/app/common/github_client.py
import httpx
import logging
from typing import List, Dict, Any, Optional
import tarfile
import zipfile
import io # Để làm việc với bytes stream
from pathlib import Path

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

    async def get_repository_archive_link(
        self, owner: str, repo: str, archive_format: str = "tarball", ref: Optional[str] = None
    ) -> Optional[str]:
        """
        Lấy URL để tải xuống một archive (tarball hoặc zipball) của repository.
        API này trả về một Redirect (302) đến URL thực tế của archive.
        archive_format: "tarball" hoặc "zipball"
        ref: Git ref (branch, tag, commit SHA)
        """
        if ref is None: # Nếu không có ref, GitHub mặc định là default branch
            endpoint_ref_part = ""
        else:
            endpoint_ref_part = f"/{ref}"

        # Chú ý: GitHub API trả về 302 Redirect, client cần theo dõi redirect
        url = f"{GITHUB_API_BASE_URL}/repos/{owner}/{repo}/{archive_format}{endpoint_ref_part}"
        logger.info(f"Requesting archive link: {url} (expecting 302 redirect)")

        async with httpx.AsyncClient(follow_redirects=False) as client: # Không tự động follow redirect
            try:
                # Truyền headers của client, vì đây vẫn là GitHub API endpoint
                response = await client.get(url, headers=self.default_json_headers, timeout=30.0)
                if response.status_code == 302: # Found (Redirect)
                    archive_url = response.headers.get("Location")
                    if archive_url:
                        logger.info(f"Obtained archive download URL: {archive_url}")
                        return archive_url
                    else:
                        logger.error("GitHub API returned 302 for archive link, but no Location header found.")
                        return None
                else:
                    # Nếu không phải 302, có thể là lỗi (404 nếu repo/ref không tồn tại)
                    logger.error(f"GitHub API did not redirect for archive link. Status: {response.status_code}, Response: {response.text[:200]}")
                    response.raise_for_status() # Để raise lỗi nếu là 4xx/5xx
                    return None # Sẽ không đến đây nếu raise_for_status() hoạt động
            except httpx.HTTPStatusError as e:
                error_details = e.response.text[:500]
                logger.error(f"GitHub API Error getting archive link: {e.response.status_code} - {e.request.url} - Response: {error_details}")
                return None
            except Exception as e:
                logger.exception(f"Unexpected error getting archive link from {url}")
                return None
    
    async def download_and_extract_archive(self, archive_url: str, extract_to_path: str):
        """
        Tải xuống archive từ URL và giải nén vào extract_to_path.
        Xử lý cả tar.gz và .zip.
        """
        logger.info(f"Downloading archive from {archive_url} and extracting to {extract_to_path}")
        Path(extract_to_path).mkdir(parents=True, exist_ok=True)

        async with httpx.AsyncClient(follow_redirects=True, timeout=300.0) as client: # Theo dõi redirect cho URL archive, tăng timeout
            try:
                response = await client.get(archive_url)
                response.raise_for_status()
                content_bytes = response.content

                archive_stream = io.BytesIO(content_bytes)

                if archive_url.endswith(".zip") or "zipball" in archive_url:
                    logger.debug("Extracting ZIP archive...")
                    with zipfile.ZipFile(archive_stream, 'r') as zip_ref:
                        # GitHub zipballs thường có một thư mục gốc bên trong
                        # Ví dụ: owner-repo-commitsha/
                        # Chúng ta muốn giải nén nội dung của thư mục gốc đó ra extract_to_path
                        # mà không tạo thêm thư mục gốc đó.
                        members = zip_ref.namelist()
                        root_folder_name = ""
                        if members and '/' in members[0] and members[0].count('/') == 1 and members[0].endswith('/'):
                            root_folder_name = members[0]

                        for member_info in zip_ref.infolist():
                            if root_folder_name and member_info.filename.startswith(root_folder_name):
                                # Loại bỏ thư mục gốc khỏi đường dẫn đích
                                target_path = Path(extract_to_path) / member_info.filename[len(root_folder_name):]
                            else:
                                target_path = Path(extract_to_path) / member_info.filename

                            if member_info.is_dir():
                                target_path.mkdir(parents=True, exist_ok=True)
                            else:
                                target_path.parent.mkdir(parents=True, exist_ok=True)
                                with open(target_path, "wb") as f_out:
                                    f_out.write(zip_ref.read(member_info.filename))
                    logger.info("ZIP archive extracted successfully.")

                elif archive_url.endswith(".tar.gz") or "tarball" in archive_url:
                    logger.debug("Extracting TAR.GZ archive...")
                    archive_stream.seek(0) # Reset stream position
                    with tarfile.open(fileobj=archive_stream, mode="r:gz") as tar_ref:
                        # Tương tự như zip, loại bỏ thư mục gốc nếu có
                        members_to_extract = []
                        root_folder_name = ""
                        # Lấy member đầu tiên để kiểm tra thư mục gốc
                        first_member = tar_ref.next()
                        if first_member and first_member.isdir() and first_member.name.count('/') == 0 :
                            root_folder_name = first_member.name + "/" # ví dụ "owner-repo-commitsha/"
                            logger.debug(f"Detected root folder in tar: {root_folder_name}")
                        tar_ref.extractall(path=extract_to_path, members=self._members_without_root(tar_ref, root_folder_name))

                    logger.info("TAR.GZ archive extracted successfully.")
                else:
                    raise ValueError(f"Unsupported archive format or could not determine format from URL: {archive_url}")

            except httpx.HTTPStatusError as e_download:
                logger.error(f"HTTP error downloading archive {archive_url}: {e_download.response.status_code}")
                raise
            except (zipfile.BadZipFile, tarfile.TarError) as e_extract:
                logger.error(f"Error extracting archive {archive_url}: {e_extract}")
                raise
            except Exception as e:
                logger.exception(f"Unexpected error downloading/extracting archive {archive_url}")
                raise
    
    def _members_without_root(self, tf: tarfile.TarFile, root_folder_name: str):
        """Helper for tarfile extraction to strip the top-level directory."""
        if not root_folder_name:
            for member in tf.getmembers():
                yield member
            return

        for member in tf.getmembers():
            if member.path.startswith(root_folder_name):
                member.path = member.path[len(root_folder_name):]
                if member.path: # Không yield entry rỗng (thư mục gốc sau khi strip)
                    yield member
    
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