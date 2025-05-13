import unittest
from unittest.mock import patch, MagicMock, AsyncMock, ANY
import json
from datetime import datetime, timezone, timedelta

from sqlalchemy.orm import Session

# Các đối tượng cần import từ app
from app.models import User, Project, PRAnalysisRequest, PRAnalysisStatus
from app.core.config import get_settings
from app.analysis_worker import consumer # Module consumer để test process_message_logic
# Giả sử consumer.py có AppSessionLocal từ app.core.db
from app.core.db import SessionLocal as AppSessionLocalForTest 

class TestAnalysisWorkerConsumerLogic(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.settings = get_settings() # Lấy settings thật để test

        self.mock_kafka_message_value = {
            "pr_analysis_request_id": 1,
            "project_id": 10,
            "user_id": 100,
            "github_repo_id": "gh_repo_123",
            "pr_number": 5,
            "head_sha": "kafka_head_sha", # SHA từ message Kafka
            "diff_url": "http://example.com/diff/5" # Không dùng trực tiếp trong logic mới
        }

        # Mock các đối tượng DB sẽ được trả về bởi CRUD/query
        self.mock_db_pr_request = MagicMock(spec=PRAnalysisRequest)
        self.mock_db_pr_request.id = self.mock_kafka_message_value["pr_analysis_request_id"]
        self.mock_db_pr_request.project_id = self.mock_kafka_message_value["project_id"]
        self.mock_db_pr_request.pr_number = self.mock_kafka_message_value["pr_number"]
        self.mock_db_pr_request.status = PRAnalysisStatus.PENDING
        self.mock_db_pr_request.head_sha = None # Ban đầu có thể là None hoặc khác với message
        self.mock_db_pr_request.pr_title = "Old Title"
        self.mock_db_pr_request.pr_github_url = "http://old.url/pr/5"

        self.mock_db_project = MagicMock(spec=Project)
        self.mock_db_project.id = self.mock_kafka_message_value["project_id"]
        self.mock_db_project.repo_name = "testowner/testrepo-slug" # Phải có /
        self.mock_db_project.language = "Python"
        self.mock_db_project.custom_project_notes = "Project custom notes for context"

        self.mock_db_user = MagicMock(spec=User)
        self.mock_db_user.id = self.mock_kafka_message_value["user_id"]
        self.mock_db_user.github_access_token_encrypted = "super_secret_encrypted_gh_token"
        
        # Mock DB session được cung cấp cho process_message_logic
        self.mock_db_for_process = MagicMock(spec=Session)


    @patch("app.analysis_worker.consumer.crud_pr_analysis.get_pr_analysis_request_by_id")
    @patch("app.analysis_worker.consumer.crud_pr_analysis.update_pr_analysis_request_status")
    @patch("app.analysis_worker.consumer.decrypt_data")
    @patch("app.analysis_worker.consumer.GitHubAPIClient") # Patch class GitHubAPIClient
    async def test_process_message_logic_success_fetches_data_and_updates_status(
        self, MockGitHubAPIClient: MagicMock, mock_decrypt: MagicMock, 
        mock_update_status: MagicMock, mock_get_pr_req: MagicMock
    ):
        # --- Setup mocks cho một luồng thành công ---
        mock_get_pr_req.return_value = self.mock_db_pr_request
        
        # Mock cho query Project và User
        # Giả sử db.query(Project).filter(...).first() và db.query(User).filter(...).first()
        # Để đơn giản, chúng ta sẽ cấu hình return_value cho filter().first() trực tiếp
        # trên self.mock_db_for_process khi nó được sử dụng.
        # Tuy nhiên, với cách viết hiện tại của process_message_logic, chúng ta cần cấu hình
        # self.mock_db_for_process.query().filter().first()
        
        # Setup return_value cho self.mock_db_for_process.query(...).filter(...).first()
        # Khi query(Project)
        mock_query_project = MagicMock()
        mock_query_project.filter.return_value.first.return_value = self.mock_db_project
        # Khi query(User)
        mock_query_user = MagicMock()
        mock_query_user.filter.return_value.first.return_value = self.mock_db_user

        # Cấu hình self.mock_db_for_process.query để trả về các mock query tương ứng
        def query_side_effect(model_class):
            if model_class == Project:
                return mock_query_project
            if model_class == User:
                return mock_query_user
            return MagicMock() # Fallback
        self.mock_db_for_process.query.side_effect = query_side_effect
        
        mock_decrypt.return_value = "decrypted_actual_github_token"

        # Mock instance của GitHubAPIClient và các phương thức của nó
        mock_gh_client_instance = MockGitHubAPIClient.return_value
        # Dữ liệu trả về từ GitHub API
        github_pr_details_response = {
            "title": "New PR Title from GitHub API", 
            "head": {"sha": "new_correct_head_sha"}, 
            "html_url": "http://new.github.url/pr/5",
            "diff_url": "http://new.github.url/pr/5.diff", # Để test có lấy cái này không
            "body": "PR description"
        }
        github_pr_diff_response = "PR diff content here..."
        github_pr_files_response = [
            {"filename": "file1.py", "status": "modified", "patch": "patch1", "raw_url": "url1"},
            {"filename": "file2.txt", "status": "added", "patch": "patch2", "raw_url": "url2"},
            {"filename": "file3.md", "status": "removed"}
        ]
        mock_gh_client_instance.get_pull_request_details = AsyncMock(return_value=github_pr_details_response)
        mock_gh_client_instance.get_pull_request_diff = AsyncMock(return_value=github_pr_diff_response)
        mock_gh_client_instance.get_pull_request_files = AsyncMock(return_value=github_pr_files_response)
        
        async def mocked_get_file_content(owner, repo, file_path, ref):
            if file_path == "file1.py": return "python content"
            if file_path == "file2.txt": return "text content"
            return None # Cho file removed hoặc lỗi
        mock_gh_client_instance.get_file_content = AsyncMock(side_effect=mocked_get_file_content)

        # --- Gọi hàm cần test ---
        # Giả sử fetch_pr_data_from_github được gọi bên trong process_message_logic
        # và process_message_logic cũng được gọi
        with patch("app.analysis_worker.consumer.fetch_pr_data_from_github", new_callable=AsyncMock) as mock_fetch_pr_data:
            # Cấu hình giá trị trả về cho fetch_pr_data_from_github
            mock_fetch_pr_data.return_value = {
                "pr_metadata": github_pr_details_response,
                "head_sha": "new_correct_head_sha",
                "pr_diff": github_pr_diff_response,
                "changed_files": [ # Dữ liệu file đã có content
                    {"filename": "file1.py", "status": "modified", "patch": "patch1", "content": "python content"},
                    {"filename": "file2.txt", "status": "added", "patch": "patch2", "content": "text content"},
                    {"filename": "file3.md", "status": "removed", "patch": None, "content": None}
                ]
            }
            await consumer.process_message_logic(self.mock_kafka_message_value, self.mock_db_for_process)

        # --- Assertions ---
        mock_get_pr_req.assert_called_once_with(self.mock_db_for_process, self.mock_kafka_message_value["pr_analysis_request_id"])
        self.assertEqual(mock_update_status.call_count, 2) # PROCESSING và DATA_FETCHED
        mock_update_status.assert_any_call(self.mock_db_for_process, self.mock_db_pr_request.id, PRAnalysisStatus.PROCESSING, error_message=None)
        mock_update_status.assert_any_call(self.mock_db_for_process, self.mock_db_pr_request.id, PRAnalysisStatus.DATA_FETCHED)
        
        mock_decrypt.assert_called_once_with("super_secret_encrypted_gh_token")
        MockGitHubAPIClient.assert_called_once_with(token="decrypted_actual_github_token")
        
        # Kiểm tra fetch_pr_data_from_github đã được gọi đúng
        mock_fetch_pr_data.assert_called_once_with(
            mock_gh_client_instance, # instance của GitHubAPIClient
            "testowner", "testrepo-slug", # owner, repo_slug
            self.mock_kafka_message_value["pr_number"], # pr_number
            self.mock_kafka_message_value["head_sha"] # head_sha_from_webhook
        )

        # Kiểm tra các thuộc tính của db_pr_request đã được cập nhật
        self.assertEqual(self.mock_db_pr_request.pr_title, github_pr_details_response["title"])
        self.assertEqual(self.mock_db_pr_request.pr_github_url, github_pr_details_response["html_url"])
        self.assertEqual(self.mock_db_pr_request.head_sha, "new_correct_head_sha")
        # Kiểm tra commit được gọi (ít nhất 1 lần cho việc cập nhật pr_request sau fetch, và 2 lần cho update_status)
        self.assertGreaterEqual(self.mock_db_for_process.commit.call_count, 1)


    @patch("app.analysis_worker.consumer.crud_pr_analysis.get_pr_analysis_request_by_id")
    @patch("app.analysis_worker.consumer.crud_pr_analysis.update_pr_analysis_request_status")
    @patch("app.analysis_worker.consumer.fetch_pr_data_from_github", new_callable=AsyncMock) # Mock cả hàm fetch
    async def test_process_message_logic_fetch_data_raises_exception(
        self, mock_fetch_pr_data: AsyncMock, mock_update_status: MagicMock, mock_get_pr_req: MagicMock
    ):
        mock_get_pr_req.return_value = self.mock_db_pr_request
        # Cấu hình query Project và User trả về giá trị hợp lệ
        mock_query_project = MagicMock(); mock_query_project.filter.return_value.first.return_value = self.mock_db_project
        mock_query_user = MagicMock(); mock_query_user.filter.return_value.first.return_value = self.mock_db_user
        def query_side_effect(model_class):
            if model_class == Project: return mock_query_project
            if model_class == User: return mock_query_user
            return MagicMock()
        self.mock_db_for_process.query.side_effect = query_side_effect

        # Giả lập fetch_pr_data_from_github raise lỗi
        error_message = "Simulated GitHub API failure during fetch"
        mock_fetch_pr_data.side_effect = Exception(error_message)

        await consumer.process_message_logic(self.mock_kafka_message_value, self.mock_db_for_process)

        self.assertEqual(mock_update_status.call_count, 2) # PROCESSING, FAILED
        mock_update_status.assert_any_call(self.mock_db_for_process, self.mock_db_pr_request.id, PRAnalysisStatus.PROCESSING, error_message=None)
        # Lấy args của lần gọi cuối cùng để kiểm tra error_message
        final_update_call_args = mock_update_status.call_args_list[-1][0] # Lấy positional args (db, req_id, status, error_msg)
        self.assertEqual(final_update_call_args[2], PRAnalysisStatus.FAILED)
        self.assertIn(error_message, final_update_call_args[3])


    # Thêm các test cases khác:
    # - test_process_message_logic_pr_request_not_found
    # - test_process_message_logic_project_not_found
    # - test_process_message_logic_user_or_token_missing
    # - test_process_message_logic_token_decrypt_fail
    # - test_process_message_logic_invalid_repo_name_format
    # - test_process_message_logic_pr_status_not_processable

    # Ví dụ cho một trường hợp lỗi khác:
    @patch("app.analysis_worker.consumer.crud_pr_analysis.get_pr_analysis_request_by_id")
    @patch("app.analysis_worker.consumer.crud_pr_analysis.update_pr_analysis_request_status")
    async def test_process_message_logic_pr_request_not_in_pending_state(self, mock_update_status: MagicMock, mock_get_pr_req: MagicMock):
        self.mock_db_pr_request.status = PRAnalysisStatus.COMPLETED # Giả sử PR đã completed
        mock_get_pr_req.return_value = self.mock_db_pr_request
        
        await consumer.process_message_logic(self.mock_kafka_message_value, self.mock_db_for_process)
        
        mock_get_pr_req.assert_called_once()
        mock_update_status.assert_not_called() # Không nên làm gì nếu status không phải PENDING/FAILED/DATA_FETCHED


if __name__ == '__main__':
    unittest.main()