import hmac
import hashlib
import json
import unittest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app # app FastAPI
from app.core.config import settings
from app.core.db import get_db as actual_get_db # Import get_db gốc để override
from app.models import User, Project
# Schemas được dùng để tạo payload mẫu và kiểm tra response (không trực tiếp trong mock)
# from app.webhook_service.schemas_pr_analysis import GitHubWebhookPayload

# Lưu giá trị gốc của webhook secret để khôi phục sau test suite
ORIGINAL_GITHUB_WEBHOOK_SECRET = settings.GITHUB_WEBHOOK_SECRET
TEST_GITHUB_WEBHOOK_SECRET = "test_secret_123"

# Payload mẫu (rút gọn và chuẩn hóa)
mock_pr_payload_dict = {
    "action": "opened",
    "number": 1347, # Thường thì action "opened" sẽ có number này trùng với pull_request.number
    "pull_request": {
        "url": "https://api.github.com/repos/octocat/Hello-World/pulls/1347",
        "id": 123456789, # ID của PR trên GitHub
        "number": 1347, # Số PR hiển thị trên UI
        "title": "Update the README with new information",
        "user": {"login": "octocat", "id": 1},
        "state": "open",
        "created_at": datetime.now(timezone.utc).isoformat(), # ISO format string
        "updated_at": datetime.now(timezone.utc).isoformat(), # ISO format string
        "html_url": "https://github.com/octocat/Hello-World/pull/1347",
        "diff_url": "https://github.com/octocat/Hello-World/pull/1347.diff",
        "head": {"sha": "0d1a26e67d8f5eaf1f6ba5c57fc3c7d91ac0fd1c"},
        "base": {"ref": "main", "sha": "0d1a26f67d8f5eaf1f6ba5c57fc3c7d91ac0aaaa"} # SHA của nhánh base
    },
    "repository": {
        "id": 1296269, # ID của repository
        "name": "Hello-World",
        "full_name": "octocat/Hello-World"
    },
    "sender": {"login": "octocat", "id": 1}
}

def generate_github_signature(payload_body: bytes, secret: str) -> str:
    """Helper để tạo signature giống GitHub."""
    hashed_payload = hmac.new(secret.encode('utf-8'), payload_body, hashlib.sha256).hexdigest()
    return f"sha256={hashed_payload}"


class TestGitHubWebhookAPI(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Ghi đè secret một lần cho toàn bộ class test
        settings.GITHUB_WEBHOOK_SECRET = TEST_GITHUB_WEBHOOK_SECRET

    @classmethod
    def tearDownClass(cls):
        # Khôi phục secret gốc sau khi tất cả test trong class đã chạy
        settings.GITHUB_WEBHOOK_SECRET = ORIGINAL_GITHUB_WEBHOOK_SECRET

    def setUp(self):
        # Tạo một mock DB session mới cho mỗi test
        self.mock_db_session = MagicMock(spec=Session)
        
        # Hàm override cho get_db
        # Đảm bảo hàm này là async vì get_db gốc là một generator (FastAPI coi là async compatible)
        async def override_get_db():
            try:
                yield self.mock_db_session
            finally:
                # Trong test, mock_db_session.close() thường không cần thiết
                pass 
        
        # Override dependency get_db cho app instance
        # Lưu lại giá trị override cũ nếu có và khôi phục sau
        self.previous_overrides = app.dependency_overrides.copy()
        app.dependency_overrides[actual_get_db] = override_get_db
        
        # Tạo TestClient SAU KHI đã thiết lập dependency_overrides
        self.client = TestClient(app)

    def tearDown(self):
        # Xóa override để không ảnh hưởng test khác (quan trọng)
        app.dependency_overrides = self.previous_overrides
        
    @patch("app.webhook_service.api.crud_pr_analysis.create_pr_analysis_request")
    @patch("app.webhook_service.api.send_pr_analysis_task", new_callable=AsyncMock)
    def test_handle_github_webhook_pr_opened_success(
        self, mock_send_kafka: AsyncMock, mock_create_pr_req: MagicMock
    ):
        # Chuẩn bị payload và signature
        payload_bytes = json.dumps(mock_pr_payload_dict).encode('utf-8')
        signature = generate_github_signature(payload_bytes, TEST_GITHUB_WEBHOOK_SECRET)

        # --- Setup Mocks cho DB query và các hàm được gọi ---
        # 1. Mock cho db.query(User, Project)...all()
        mock_user = MagicMock(spec=User)
        mock_user.id = 1
        mock_user.email = "owner@example.com"
        mock_user.github_access_token_encrypted = "encrypted_token_data"

        mock_project = MagicMock(spec=Project)
        mock_project.id = 101
        mock_project.user_id = mock_user.id
        mock_project.github_repo_id = str(mock_pr_payload_dict["repository"]["id"])
        mock_project.repo_name = mock_pr_payload_dict["repository"]["full_name"]
        
        self.mock_db_session.query(User, Project).join(Project, User.id == Project.user_id).filter().all.return_value = [(mock_user, mock_project)]
        
        # 2. Mock cho crud_pr_analysis.create_pr_analysis_request
        mock_db_pr_analysis_request = MagicMock()
        mock_db_pr_analysis_request.id = 501
        mock_create_pr_req.return_value = mock_db_pr_analysis_request
        
        # 3. Mock cho send_pr_analysis_task
        mock_send_kafka.return_value = True

        # --- Thực hiện Request ---
        headers = {
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": signature,
            "Content-Type": "application/json"
        }
        
        response = self.client.post("/webhooks/github", content=payload_bytes, headers=headers)

        # --- Assertions ---
        self.assertEqual(response.status_code, 202, f"Response JSON: {response.json()}")
        self.assertIn("1 analysis task(s) queued", response.json()["message"])

        self.mock_db_session.query(User, Project).join(Project, User.id == Project.user_id).filter().all.assert_called_once()
        
        mock_create_pr_req.assert_called_once()
        # Lấy cả args và kwargs từ call_args
        called_args_crud, called_kwargs_crud = mock_create_pr_req.call_args
        
        # db là positional argument đầu tiên
        self.assertIs(called_args_crud[0], self.mock_db_session) 
        
        # request_in là keyword argument
        self.assertIn("request_in", called_kwargs_crud) # Kiểm tra key 'request_in' có trong kwargs không
        created_req_in_schema = called_kwargs_crud['request_in'] # Lấy giá trị từ kwargs
        
        self.assertEqual(created_req_in_schema.project_id, mock_project.id)
        self.assertEqual(created_req_in_schema.pr_number, mock_pr_payload_dict["pull_request"]["number"])
        self.assertEqual(created_req_in_schema.pr_title, mock_pr_payload_dict["pull_request"]["title"])
        self.assertEqual(str(created_req_in_schema.pr_github_url), mock_pr_payload_dict["pull_request"]["html_url"])
        self.assertEqual(created_req_in_schema.head_sha, mock_pr_payload_dict["pull_request"]["head"]["sha"])
        
        mock_send_kafka.assert_called_once()
        called_args_kafka, _ = mock_send_kafka.call_args # send_pr_analysis_task chỉ có 1 positional arg là task_data
        sent_kafka_data = called_args_kafka[0]

        self.assertEqual(sent_kafka_data["pr_analysis_request_id"], mock_db_pr_analysis_request.id)
        self.assertEqual(sent_kafka_data["project_id"], mock_project.id)
        self.assertEqual(sent_kafka_data["user_id"], mock_user.id)
        self.assertEqual(sent_kafka_data["github_repo_id"], str(mock_pr_payload_dict["repository"]["id"]))
        self.assertEqual(sent_kafka_data["pr_number"], mock_pr_payload_dict["pull_request"]["number"])
        self.assertEqual(sent_kafka_data["head_sha"], mock_pr_payload_dict["pull_request"]["head"]["sha"])
        self.assertEqual(sent_kafka_data["diff_url"], mock_pr_payload_dict["pull_request"]["diff_url"])

    def test_handle_github_webhook_invalid_signature(self):
        payload_bytes = json.dumps(mock_pr_payload_dict).encode('utf-8')
        headers = {
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": "sha256=invalid_signature_dummy",
            "Content-Type": "application/json"
        }
        response = self.client.post("/webhooks/github", content=payload_bytes, headers=headers)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "Invalid request signature.")

    def test_handle_github_webhook_missing_signature_header(self):
        payload_bytes = json.dumps(mock_pr_payload_dict).encode('utf-8')
        headers = {
            "X-GitHub-Event": "pull_request",
            "Content-Type": "application/json"
        }
        response = self.client.post("/webhooks/github", content=payload_bytes, headers=headers)
        self.assertEqual(response.status_code, 400) 
        self.assertEqual(response.json()["detail"], "X-Hub-Signature-256 header is missing!")

    def test_handle_github_webhook_project_not_found(self):
        payload_dict_no_project = {
            **mock_pr_payload_dict, 
            "repository": {**mock_pr_payload_dict["repository"], "id": 9999} # ID repo không tồn tại
        }
        payload_bytes = json.dumps(payload_dict_no_project).encode('utf-8')
        signature = generate_github_signature(payload_bytes, TEST_GITHUB_WEBHOOK_SECRET)
        
        self.mock_db_session.query(User, Project).join(Project, User.id == Project.user_id).filter().all.return_value = []

        headers = {
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": signature,
            "Content-Type": "application/json"
        }
        response = self.client.post("/webhooks/github", content=payload_bytes, headers=headers)
        self.assertEqual(response.status_code, 202, response.json())
        self.assertEqual(response.json()["message"], "Project not found in NovaGuard")


    def test_handle_github_webhook_wrong_event_type(self):
        payload_bytes = json.dumps(mock_pr_payload_dict).encode('utf-8') # Payload có thể không quan trọng bằng header
        signature = generate_github_signature(payload_bytes, TEST_GITHUB_WEBHOOK_SECRET) # Signature vẫn cần đúng
        headers = {
            "X-GitHub-Event": "issues", # Event type sai
            "X-Hub-Signature-256": signature,
            "Content-Type": "application/json"
        }
        response = self.client.post("/webhooks/github", content=payload_bytes, headers=headers)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["message"], "Event type ignored")


    def test_handle_github_webhook_wrong_pr_action(self):
        payload_dict_wrong_action = {**mock_pr_payload_dict, "action": "closed"}
        payload_bytes = json.dumps(payload_dict_wrong_action).encode('utf-8')
        signature = generate_github_signature(payload_bytes, TEST_GITHUB_WEBHOOK_SECRET)
        headers = {
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": signature,
            "Content-Type": "application/json"
        }
        response = self.client.post("/webhooks/github", content=payload_bytes, headers=headers)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["message"], "Pull request action ignored")

if __name__ == '__main__':
    unittest.main()