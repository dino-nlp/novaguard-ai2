import unittest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timezone
import json

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.main import app
from app.core.db import get_db as actual_get_db
from app.core.config import settings
from app.models import User, Project # Import từ app.models
from app.auth_service import schemas as auth_schemas
from app.project_service import schemas as project_schemas_module
# Import CRUD trực tiếp để patch cho đúng đối tượng
from app.project_service import crud_project as crud_project_module_to_patch

# --- Mock Data & Config ---
ORIGINAL_GITHUB_WEBHOOK_SECRET = settings.GITHUB_WEBHOOK_SECRET
TEST_GITHUB_WEBHOOK_SECRET = "test_secret_for_project_api_v3"

MOCK_USER_ID = 1
mock_current_user_for_project_tests = auth_schemas.UserPublic(
    id=MOCK_USER_ID,
    email="projecttestuser@example.com",
    created_at=datetime.now(timezone.utc),
    updated_at=datetime.now(timezone.utc)
)

mock_pr_payload_dict = { # Giữ nguyên payload này từ lần trước
    "action": "opened", "number": 1347,
    "pull_request": {
        "url": "https://api.github.com/repos/octocat/Hello-World/pulls/1347", "id": 123456789,
        "number": 1347, "title": "Update the README with new information",
        "user": {"login": "octocat", "id": 1}, "state": "open",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "html_url": "https://github.com/octocat/Hello-World/pull/1347",
        "diff_url": "https://github.com/octocat/Hello-World/pull/1347.diff",
        "head": {"sha": "0d1a26e67d8f5eaf1f6ba5c57fc3c7d91ac0fd1c"},
        "base": {"ref": "main", "sha": "0d1a26f67d8f5eaf1f6ba5c57fc3c7d91ac0aaaa"}
    },
    "repository": {"id": 1296269, "name": "Hello-World", "full_name": "octocat/Hello-World"},
    "sender": {"login": "octocat", "id": 1}
}

def generate_github_signature(payload_body: bytes, secret: str) -> str:
    hashed_payload = hmac.new(secret.encode('utf-8'), payload_body, hashlib.sha256).hexdigest()
    return f"sha256={hashed_payload}"

async def override_get_current_active_user_for_project_tests_dependency(): # Đổi tên để rõ ràng là dependency
    return mock_current_user_for_project_tests

class TestProjectAPI(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        settings.GITHUB_WEBHOOK_SECRET = TEST_GITHUB_WEBHOOK_SECRET
        from app.project_service.api import get_current_active_user as project_api_dependency_get_user
        cls.project_api_get_user_dependency = project_api_dependency_get_user # Lưu lại để dùng trong tearDown
        cls.previous_auth_override = app.dependency_overrides.get(cls.project_api_get_user_dependency)
        app.dependency_overrides[cls.project_api_get_user_dependency] = override_get_current_active_user_for_project_tests_dependency

    @classmethod
    def tearDownClass(cls):
        settings.GITHUB_WEBHOOK_SECRET = ORIGINAL_GITHUB_WEBHOOK_SECRET
        if cls.previous_auth_override:
            app.dependency_overrides[cls.project_api_get_user_dependency] = cls.previous_auth_override
        elif cls.project_api_get_user_dependency in app.dependency_overrides:
            del app.dependency_overrides[cls.project_api_get_user_dependency]

    def setUp(self):
        self.mock_db_session = MagicMock(spec=Session)
        async def override_get_db():
            try:
                yield self.mock_db_session
            finally:
                pass
        self.previous_db_override = app.dependency_overrides.get(actual_get_db)
        app.dependency_overrides[actual_get_db] = override_get_db
        self.client = TestClient(app)

    def tearDown(self):
        if self.previous_db_override:
            app.dependency_overrides[actual_get_db] = self.previous_db_override
        elif actual_get_db in app.dependency_overrides:
            del app.dependency_overrides[actual_get_db]

    @patch("app.project_service.crud_project.create_project") # Patch đúng vào module CRUD
    @patch("app.project_service.api.decrypt_data")
    @patch("app.project_service.api.httpx.AsyncClient")
    def test_create_new_project_success(self, MockAsyncClient: MagicMock, mock_decrypt_data: MagicMock, mock_create_project_crud: MagicMock):
        project_payload = {
            "github_repo_id": "12345",
            "repo_name": "owner/repo-create-test",
            "main_branch": "main",
            "language": "Python"
        }

        mock_user_for_token = MagicMock(spec=User)
        mock_user_for_token.github_access_token_encrypted = "encrypted_token_data_string"
        query_user_mock = self.mock_db_session.query.return_value
        filter_user_mock = query_user_mock.filter.return_value
        filter_user_mock.first.return_value = mock_user_for_token
        
        mock_decrypt_data.return_value = "decrypted_github_token"

        # Đây là đối tượng Project SẼ ĐƯỢC TRẢ VỀ BỞI crud_project.create_project
        # và SAU ĐÓ được API cập nhật github_webhook_id
        mock_db_project_returned_by_crud = MagicMock(spec=Project)
        mock_db_project_returned_by_crud.id = 1
        mock_db_project_returned_by_crud.user_id = MOCK_USER_ID
        mock_db_project_returned_by_crud.github_repo_id = project_payload["github_repo_id"]
        mock_db_project_returned_by_crud.repo_name = project_payload["repo_name"]
        mock_db_project_returned_by_crud.main_branch = project_payload["main_branch"]
        mock_db_project_returned_by_crud.language = project_payload["language"]
        mock_db_project_returned_by_crud.custom_project_notes = None
        mock_db_project_returned_by_crud.github_webhook_id = None # Quan trọng: Khởi tạo là None
        mock_db_project_returned_by_crud.created_at = datetime.now(timezone.utc)
        mock_db_project_returned_by_crud.updated_at = datetime.now(timezone.utc)
        mock_create_project_crud.return_value = mock_db_project_returned_by_crud

        mock_http_client_instance = MockAsyncClient.return_value.__aenter__.return_value
        mock_hook_response = MagicMock()
        mock_hook_response.status_code = 201
        mock_hook_response.json.return_value = {"id": "gh_hook_id_123"}
        mock_http_client_instance.post.return_value = mock_hook_response
        
        # Mock cho db.commit() và db.refresh() để chúng không làm gì ngoài việc được gọi
        self.mock_db_session.commit = MagicMock()
        
        # Khi db.refresh(db_project) được gọi trong API,
        # chúng ta muốn đảm bảo rằng thuộc tính github_webhook_id của db_project
        # (chính là mock_db_project_returned_by_crud) đã được cập nhật.
        # Hàm refresh thực sự không làm thay đổi mock object, nhưng chúng ta cần đảm bảo
        # Pydantic đọc đúng giá trị đã được API set.
        # Chúng ta sẽ không mock refresh một cách đặc biệt ở đây, chỉ kiểm tra nó được gọi.
        self.mock_db_session.refresh = MagicMock()


        with patch.object(settings, 'NOVAGUARD_PUBLIC_URL', 'http://test.novaguard.com'):
            response = self.client.post("/projects/", json=project_payload)

        self.assertEqual(response.status_code, 201, response.json())
        data = response.json()
        self.assertEqual(data["repo_name"], project_payload["repo_name"])
        self.assertEqual(data["user_id"], MOCK_USER_ID)
        
        # Assertion quan trọng:
        self.assertEqual(data.get("github_webhook_id"), "gh_hook_id_123")

        # Kiểm tra mock_db_project_returned_by_crud đã được cập nhật github_webhook_id
        # trước khi nó được serialize và trả về.
        # Điều này gián tiếp xác nhận logic trong API.
        self.assertEqual(mock_db_project_returned_by_crud.github_webhook_id, "gh_hook_id_123")

        mock_create_project_crud.assert_called_once()
        mock_http_client_instance.post.assert_called_once()
        self.mock_db_session.commit.assert_called() # Kiểm tra commit được gọi (ít nhất 2 lần: sau create_project và sau update webhook_id)
        self.mock_db_session.refresh.assert_called_with(mock_db_project_returned_by_crud) # Kiểm tra refresh được gọi với đúng đối tượng


    @patch("app.project_service.crud_project.create_project")
    def test_create_project_conflict(self, mock_create_project_crud: MagicMock):
        mock_create_project_crud.return_value = None
        project_payload = {"github_repo_id": "gh_conflict", "repo_name": "owner/conflict", "main_branch": "main"}
        response = self.client.post("/projects/", json=project_payload)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "Project with this GitHub Repo ID already exists for this user, or another DB error occurred.")

    @patch("app.project_service.crud_project.get_projects_by_user")
    def test_read_user_projects(self, mock_get_projects_crud: MagicMock):
        mock_project_1 = MagicMock(spec=Project)
        mock_project_1.id=1; mock_project_1.user_id=MOCK_USER_ID; mock_project_1.repo_name="p1"
        mock_project_1.github_repo_id="g1"; mock_project_1.main_branch="m"; mock_project_1.language="L1"
        mock_project_1.custom_project_notes="N1"; mock_project_1.created_at=datetime.now(timezone.utc); mock_project_1.updated_at=datetime.now(timezone.utc)
        mock_project_1.github_webhook_id = "hook_for_p1"

        mock_get_projects_crud.return_value = [mock_project_1]
        
        # Mock cho db.query(func.count(Project.id)).filter(...).scalar()
        # self.mock_db_session.query() sẽ được gọi một lần cho count
        # self.mock_db_session.query(func.count(Project.id)) là đối tượng được truyền vào
        
        # Cấu hình mock chi tiết hơn cho chuỗi query count
        mock_query_obj_for_count = MagicMock()
        mock_filter_obj_for_count = MagicMock()
        
        self.mock_db_session.query.return_value = mock_query_obj_for_count # db.query(...)
        mock_query_obj_for_count.filter.return_value = mock_filter_obj_for_count # .filter(...)
        mock_filter_obj_for_count.scalar.return_value = 1 # .scalar()
        
        response = self.client.get("/projects/?skip=0&limit=10")
        
        self.assertEqual(response.status_code, 200, response.json())
        data = response.json()
        self.assertEqual(len(data["projects"]), 1)
        self.assertEqual(data["projects"][0]["repo_name"], "p1")
        self.assertEqual(data["projects"][0]["github_webhook_id"], "hook_for_p1")
        self.assertEqual(data["total"], 1)
        
        # Kiểm tra mock_get_projects_crud được gọi đúng
        mock_get_projects_crud.assert_called_once_with(db=self.mock_db_session, user_id=MOCK_USER_ID, skip=0, limit=10)
        
        # Kiểm tra self.mock_db_session.query được gọi với đối số func.count(Project.id)
        # Lấy danh sách các lần gọi và kiểm tra lần cuối cùng (hoặc lần duy nhất nếu chỉ có 1)
        # Vì crud_project.get_projects_by_user đã được mock, db.query chỉ được gọi 1 lần cho count.
        self.assertEqual(self.mock_db_session.query.call_count, 1)
        actual_call_args = self.mock_db_session.query.call_args[0] # Lấy tuple các positional args của LẦN GỌI CUỐI CÙNG (hoặc duy nhất)
        
        # Kiểm tra xem đối số đầu tiên có phải là một đối tượng sqlalchemy.sql.functions.count không
        self.assertIsInstance(actual_call_args[0], type(func.count(Project.id)))
        # Kiểm tra sâu hơn nếu cần, ví dụ tên của function hoặc đối tượng được count
        # (So sánh trực tiếp func.count(Project.id) có thể không ổn định do object identity)
        # Cách kiểm tra tốt hơn là kiểm tra cấu trúc của đối tượng expression
        self.assertEqual(str(actual_call_args[0]), str(func.count(Project.id))) # So sánh biểu diễn chuỗi

        # Kiểm tra filter và scalar đã được gọi trên các đối tượng mock tương ứng
        mock_query_obj_for_count.filter.assert_called_once() # Có thể kiểm tra điều kiện filter ở đây nếu cần
        mock_filter_obj_for_count.scalar.assert_called_once()

    @patch("app.project_service.crud_project.get_project_by_id")
    def test_read_project_details_found(self, mock_get_project_crud: MagicMock):
        project_id_to_test = 7
        mock_db_project = MagicMock(spec=Project)
        mock_db_project.id=project_id_to_test; mock_db_project.user_id=MOCK_USER_ID; mock_db_project.repo_name="detail_test"
        mock_db_project.github_repo_id="g_detail"; mock_db_project.main_branch="m_detail"; mock_db_project.language="L_detail"
        mock_db_project.custom_project_notes="N_detail"; mock_db_project.created_at=datetime.now(timezone.utc); mock_db_project.updated_at=datetime.now(timezone.utc)
        mock_db_project.github_webhook_id = "detail_hook_7" # Gán giá trị hợp lệ

        mock_get_project_crud.return_value = mock_db_project
        response = self.client.get(f"/projects/{project_id_to_test}")
        self.assertEqual(response.status_code, 200, response.json())
        data = response.json()
        self.assertEqual(data["repo_name"], "detail_test")
        self.assertEqual(data["github_webhook_id"], "detail_hook_7")
        mock_get_project_crud.assert_called_once_with(db=self.mock_db_session, project_id=project_id_to_test, user_id=MOCK_USER_ID)

    @patch("app.project_service.crud_project.get_project_by_id")
    def test_read_project_details_not_found(self, mock_get_project_crud: MagicMock):
        mock_get_project_crud.return_value = None
        response = self.client.get("/projects/999")
        self.assertEqual(response.status_code, 404)

    @patch("app.project_service.crud_project.update_project")
    def test_update_project_success(self, mock_update_project_crud: MagicMock):
        project_id_to_update = 5
        update_payload = {"repo_name": "updated_name", "language": "Rust"}
        mock_updated_db_project = MagicMock(spec=Project)
        mock_updated_db_project.id=project_id_to_update; mock_updated_db_project.user_id=MOCK_USER_ID; 
        mock_updated_db_project.repo_name=update_payload["repo_name"]; mock_updated_db_project.language=update_payload["language"]
        mock_updated_db_project.github_repo_id="g_update"; mock_updated_db_project.main_branch="m_update"; 
        mock_updated_db_project.custom_project_notes="N_update"; mock_updated_db_project.created_at=datetime.now(timezone.utc); mock_updated_db_project.updated_at=datetime.now(timezone.utc)
        mock_updated_db_project.github_webhook_id = "hook_after_update" # Gán giá trị hợp lệ

        mock_update_project_crud.return_value = mock_updated_db_project
        response = self.client.put(f"/projects/{project_id_to_update}", json=update_payload)
        self.assertEqual(response.status_code, 200, response.json())
        data = response.json()
        self.assertEqual(data["repo_name"], "updated_name")
        self.assertEqual(data["github_webhook_id"], "hook_after_update")

    @patch("app.project_service.crud_project.update_project")
    def test_update_project_not_found(self, mock_update_project_crud: MagicMock):
        mock_update_project_crud.return_value = None
        response = self.client.put("/projects/999", json={"repo_name": "test"})
        self.assertEqual(response.status_code, 404)

    @patch("app.project_service.crud_project.get_project_by_id")
    @patch("app.project_service.crud_project.delete_project")
    @patch("app.project_service.api.decrypt_data")
    @patch("app.project_service.api.httpx.AsyncClient")
    def test_delete_project_success(self, MockAsyncClientDelete: MagicMock, mock_decrypt_data_del: MagicMock, mock_delete_project_crud: MagicMock, mock_get_project_b_id_del: MagicMock):
        project_id_to_delete = 3
        mock_project_info = MagicMock(spec=Project)
        mock_project_info.id = project_id_to_delete; mock_project_info.user_id = MOCK_USER_ID
        mock_project_info.repo_name = "owner/to-be-deleted"; mock_project_info.github_webhook_id = "hook_id_to_delete"
        mock_project_info.github_repo_id="g_del"; mock_project_info.main_branch="m_del"; mock_project_info.language="L_del"
        mock_project_info.custom_project_notes="N_del"; mock_project_info.created_at=datetime.now(timezone.utc); mock_project_info.updated_at=datetime.now(timezone.utc)

        mock_get_project_b_id_del.return_value = mock_project_info
        mock_delete_project_crud.return_value = mock_project_info # Quan trọng: crud delete trả về project đã xóa

        mock_user_for_token_del = MagicMock(spec=User)
        mock_user_for_token_del.github_access_token_encrypted = "encrypted_token_for_delete"
        query_user_del_mock = self.mock_db_session.query.return_value
        filter_user_del_mock = query_user_del_mock.filter.return_value
        filter_user_del_mock.first.return_value = mock_user_for_token_del
        
        mock_decrypt_data_del.return_value = "decrypted_github_token_for_delete"

        mock_http_client_del_instance = MockAsyncClientDelete.return_value.__aenter__.return_value
        mock_del_hook_response = MagicMock()
        mock_del_hook_response.status_code = 204
        mock_http_client_del_instance.delete.return_value = mock_del_hook_response
        
        response = self.client.delete(f"/projects/{project_id_to_delete}")
        
        self.assertEqual(response.status_code, 200, response.json())
        data = response.json()
        self.assertEqual(data["repo_name"], "owner/to-be-deleted")
        self.assertEqual(data["github_webhook_id"], "hook_id_to_delete") # Kiểm tra webhook ID trong response
        
        mock_get_project_b_id_del.assert_called_once_with(self.mock_db_session, project_id_to_delete, MOCK_USER_ID)
        mock_delete_project_crud.assert_called_once_with(db=self.mock_db_session, project_id=project_id_to_delete, user_id=MOCK_USER_ID)
        mock_http_client_del_instance.delete.assert_called_once()

    @patch("app.project_service.crud_project.get_project_by_id")
    def test_delete_project_not_found(self, mock_get_project_b_id_del: MagicMock):
        mock_get_project_b_id_del.return_value = None
        response = self.client.delete("/projects/999")
        self.assertEqual(response.status_code, 404)

# if __name__ == '__main__':
#     unittest.main() # Bỏ dòng này nếu chạy bằng discover