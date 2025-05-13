# novaguard-backend/tests/common/test_github_client.py
import unittest
from unittest.mock import patch, AsyncMock, MagicMock
import httpx # Cần thiết để raise httpx exceptions trong mock
import base64 # Để test mã hóa/giải mã base64
from typing import Optional

from app.common.github_client import GitHubAPIClient, GITHUB_API_BASE_URL # GITHUB_API_VERSION_HEADER không cần thiết phải import trực tiếp

# Dùng lại cấu trúc IsolatedAsyncioTestCase nếu đã có sẵn trong các file test khác của bạn
# Hoặc tạo một class kế thừa từ unittest.TestCase và dùng asyncio.run cho mỗi test method
# Với Python 3.8+, unittest.IsolatedAsyncioTestCase là lựa chọn tốt.
class TestGitHubAPIClient(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.test_token = "fake_github_token_for_client"
        self.owner = "testowner"
        self.repo = "testrepo"
        self.pr_number = 123
        self.file_path = "src/main.py"
        self.ref = "test_commit_sha"
        self.client = GitHubAPIClient(token=self.test_token)

    def _create_mock_httpx_response(self, status_code: int, json_data: Optional[dict] = None, text_data: Optional[str] = None, headers: Optional[dict] = None):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = status_code
        if json_data is not None:
            mock_response.json.return_value = json_data
        if text_data is not None:
            mock_response.text = text_data
        # headers của httpx.Response là một httpx.Headers object, không phải dict đơn giản
        # nhưng MagicMock sẽ xử lý việc này. Nếu cần chính xác hơn, có thể tạo httpx.Headers(headers or {})
        mock_response.headers = httpx.Headers(headers or {})
        
        # Để raise_for_status() hoạt động đúng với mock
        if status_code >= 400:
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                message=f"Mock HTTP Error {status_code}",
                request=httpx.Request("GET", "http://mockurl.com"), # Cần một request object
                response=mock_response
            )
        else:
            mock_response.raise_for_status = MagicMock() # Không làm gì nếu status < 400
        return mock_response

    async def test_init_requires_token(self):
        with self.assertRaisesRegex(ValueError, "GitHub token is required for APIClient."):
            GitHubAPIClient(token="")
        with self.assertRaisesRegex(ValueError, "GitHub token is required for APIClient."):
            GitHubAPIClient(token=None) # type: ignore

    @patch("app.common.github_client.httpx.AsyncClient")
    async def test_get_pull_request_details_success(self, MockAsyncHttpxClient):
        mock_api_response_data = {"id": self.pr_number, "title": "Test PR", "head": {"sha": self.ref}}
        mock_http_response = self._create_mock_httpx_response(200, json_data=mock_api_response_data)
        
        mock_httpx_client_instance = MockAsyncHttpxClient.return_value.__aenter__.return_value
        mock_httpx_client_instance.request.return_value = mock_http_response

        details = await self.client.get_pull_request_details(self.owner, self.repo, self.pr_number)

        expected_url = f"{GITHUB_API_BASE_URL}/repos/{self.owner}/{self.repo}/pulls/{self.pr_number}"
        mock_httpx_client_instance.request.assert_called_once_with(
            "GET", expected_url, headers=self.client.headers
        )
        self.assertEqual(details, mock_api_response_data)

    @patch("app.common.github_client.httpx.AsyncClient")
    async def test_get_pull_request_details_failure(self, MockAsyncHttpxClient):
        mock_http_response = self._create_mock_httpx_response(404, json_data={"message": "Not Found"})
        mock_httpx_client_instance = MockAsyncHttpxClient.return_value.__aenter__.return_value
        mock_httpx_client_instance.request.return_value = mock_http_response # raise_for_status sẽ được gọi

        details = await self.client.get_pull_request_details(self.owner, self.repo, self.pr_number)
        self.assertIsNone(details) # Vì _request bắt exception và trả về None

    @patch("app.common.github_client.httpx.AsyncClient")
    async def test_get_pull_request_diff_success(self, MockAsyncHttpxClient):
        mock_diff_data = "diff --git a/file.txt b/file.txt"
        mock_http_response = self._create_mock_httpx_response(200, text_data=mock_diff_data)
        mock_httpx_client_instance = MockAsyncHttpxClient.return_value.__aenter__.return_value
        mock_httpx_client_instance.request.return_value = mock_http_response

        diff = await self.client.get_pull_request_diff(self.owner, self.repo, self.pr_number)
        
        expected_url = f"{GITHUB_API_BASE_URL}/repos/{self.owner}/{self.repo}/pulls/{self.pr_number}"
        mock_httpx_client_instance.request.assert_called_once_with(
            "GET", expected_url, headers=self.client.diff_headers # Kiểm tra đúng headers
        )
        self.assertEqual(diff, mock_diff_data)

    @patch("app.common.github_client.httpx.AsyncClient")
    async def test_get_pull_request_files_single_page(self, MockAsyncHttpxClient):
        mock_files_data = [{"filename": "file1.py", "status": "modified", "patch": "...", "raw_url": "..."}]
        mock_http_response = self._create_mock_httpx_response(200, json_data=mock_files_data, headers={})
        mock_httpx_client_instance = MockAsyncHttpxClient.return_value.__aenter__.return_value
        mock_httpx_client_instance.request.return_value = mock_http_response

        files = await self.client.get_pull_request_files(self.owner, self.repo, self.pr_number)
        
        expected_url = f"{GITHUB_API_BASE_URL}/repos/{self.owner}/{self.repo}/pulls/{self.pr_number}/files?per_page=100&page=1"
        mock_httpx_client_instance.request.assert_called_once_with(
            "GET", expected_url, headers=self.client.headers
        )
        self.assertEqual(files, mock_files_data)

    @patch("app.common.github_client.httpx.AsyncClient")
    async def test_get_pull_request_files_multiple_pages(self, MockAsyncHttpxClient):
        mock_files_page1 = [{"filename": f"file_p1_{i}.py"} for i in range(100)]
        mock_files_page2 = [{"filename": "file_p2_final.py"}]
        
        next_page_url = f"{GITHUB_API_BASE_URL}/repos/{self.owner}/{self.repo}/pulls/{self.pr_number}/files?per_page=100&page=2"
        link_header_page1 = f'<{next_page_url}>; rel="next", <http://example.com/last>; rel="last"'
        
        response_page1 = self._create_mock_httpx_response(200, json_data=mock_files_page1, headers={"Link": link_header_page1})
        response_page2 = self._create_mock_httpx_response(200, json_data=mock_files_page2, headers={})

        mock_httpx_client_instance = MockAsyncHttpxClient.return_value.__aenter__.return_value
        mock_httpx_client_instance.request.side_effect = [response_page1, response_page2]

        files = await self.client.get_pull_request_files(self.owner, self.repo, self.pr_number)

        self.assertEqual(len(files), 101)
        self.assertEqual(files[0]['filename'], "file_p1_0.py")
        self.assertEqual(files[-1]['filename'], "file_p2_final.py")
        self.assertEqual(mock_httpx_client_instance.request.call_count, 2)
        # Kiểm tra URL của lần gọi thứ hai
        self.assertEqual(mock_httpx_client_instance.request.call_args_list[1][0][1], next_page_url)


    @patch("app.common.github_client.httpx.AsyncClient")
    async def test_get_file_content_text_base64(self, MockAsyncHttpxClient):
        original_content = "print('Hello from NovaGuard-AI!')"
        base64_content = base64.b64encode(original_content.encode('utf-8')).decode('utf-8')
        mock_api_response = {"content": base64_content, "encoding": "base64", "name": self.file_path}
        
        mock_http_response = self._create_mock_httpx_response(200, json_data=mock_api_response)
        mock_httpx_client_instance = MockAsyncHttpxClient.return_value.__aenter__.return_value
        mock_httpx_client_instance.request.return_value = mock_http_response

        content = await self.client.get_file_content(self.owner, self.repo, self.file_path, self.ref)
        
        expected_url = f"{GITHUB_API_BASE_URL}/repos/{self.owner}/{self.repo}/contents/{self.file_path}"
        mock_httpx_client_instance.request.assert_called_once_with(
            "GET", expected_url, headers=self.client.headers, params={"ref": self.ref}
        )
        self.assertEqual(content, original_content)

    @patch("app.common.github_client.httpx.AsyncClient")
    async def test_get_file_content_binary_gives_placeholder(self, MockAsyncHttpxClient):
        # Tạo một chuỗi base64 mà khi decode sẽ lỗi UTF-8
        invalid_utf8_bytes = b'\x80\x81\x82' 
        base64_content_binary = base64.b64encode(invalid_utf8_bytes).decode('utf-8')
        mock_api_response = {"content": base64_content_binary, "encoding": "base64", "name": "binary.file"}

        mock_http_response = self._create_mock_httpx_response(200, json_data=mock_api_response)
        mock_httpx_client_instance = MockAsyncHttpxClient.return_value.__aenter__.return_value
        mock_httpx_client_instance.request.return_value = mock_http_response

        content = await self.client.get_file_content(self.owner, self.repo, "binary.file", self.ref)
        self.assertEqual(content, "[Binary Content - Not Decoded]")

    @patch("app.common.github_client.httpx.AsyncClient")
    async def test_get_file_content_download_url(self, MockAsyncHttpxClient):
        mock_download_url = "https://example.com/raw/file.txt"
        mock_raw_content = "This is raw content from download_url."
        mock_api_response_no_content = {"download_url": mock_download_url, "name": self.file_path, "encoding": None, "content": None}

        response_for_contents_api = self._create_mock_httpx_response(200, json_data=mock_api_response_no_content)
        response_for_download_url = self._create_mock_httpx_response(200, text_data=mock_raw_content)

        mock_httpx_client_instance = MockAsyncHttpxClient.return_value.__aenter__.return_value
        # Lần gọi đầu cho /contents, lần gọi sau cho download_url
        mock_httpx_client_instance.request.side_effect = [response_for_contents_api, response_for_download_url]

        content = await self.client.get_file_content(self.owner, self.repo, self.file_path, self.ref)

        self.assertEqual(mock_httpx_client_instance.request.call_count, 2)
        self.assertEqual(mock_httpx_client_instance.request.call_args_list[1][0][1], mock_download_url) # Kiểm tra URL của lần gọi thứ 2
        self.assertEqual(content, mock_raw_content)

if __name__ == '__main__':
    unittest.main()