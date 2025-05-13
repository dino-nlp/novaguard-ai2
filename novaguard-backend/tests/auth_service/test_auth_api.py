import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session # Cần cho type hinting

# Import app FastAPI chính và get_db dependency
from app.main import app # app FastAPI
from app.core.db import get_db # dependency get_db
from app.auth_service import schemas # schemas Pydantic
from app.models import User # model User SQLAlchemy

# --- Test Setup ---
# Chúng ta cần override dependency get_db để sử dụng một DB test hoặc mock DB
# For unit/integration tests, a separate test database is often used.
# Here, we'll demonstrate mocking the CRUD functions called by the API.

# Tạo TestClient
client = TestClient(app)

# Dữ liệu mẫu
test_user_email = "testapi@example.com"
test_user_password = "testapipassword"
test_user_hashed_password = "hashed_testapipassword" # Giả sử đã hash

class TestAuthAPI(unittest.TestCase):

    @patch("app.auth_service.api.crud_user.get_user_by_email")
    @patch("app.auth_service.api.crud_user.create_user")
    def test_register_new_user_success(self, mock_create_user: MagicMock, mock_get_user_by_email: MagicMock):
        """Test đăng ký user mới thành công."""
        mock_get_user_by_email.return_value = None 
        
        # Lấy thời gian hiện tại để gán cho created_at và updated_at
        now = datetime.now(timezone.utc) # Sử dụng timezone aware datetime

        mock_created_user_db_obj = User(
            id=1, 
            email=test_user_email, 
            password_hash=test_user_hashed_password,
            created_at=now,  # Gán giá trị datetime
            updated_at=now   # Gán giá trị datetime
        )
        mock_create_user.return_value = mock_created_user_db_obj

        response = client.post(
            "/auth/register",
            json={"email": test_user_email, "password": test_user_password},
        )
        
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["email"], test_user_email)
        self.assertNotIn("password_hash", data)
        self.assertIn("id", data)
        # Kiểm tra xem created_at và updated_at có trong response và là chuỗi ISO format không
        self.assertIn("created_at", data)
        self.assertIn("updated_at", data)
        # self.assertEqual(data["created_at"], now.isoformat().replace("+00:00", "Z")) # Pydantic V2 serialize to string
        # self.assertEqual(data["updated_at"], now.isoformat().replace("+00:00", "Z")) # Pydantic V2 serialize to string

        mock_get_user_by_email.assert_called_once_with(unittest.mock.ANY, email=test_user_email)
        mock_create_user.assert_called_once()
        args, kwargs = mock_create_user.call_args
        self.assertEqual(kwargs['user'].email, test_user_email)
        self.assertEqual(kwargs['user'].password, test_user_password)


    @patch("app.auth_service.api.crud_user.get_user_by_email")
    def test_register_user_email_exists(self, mock_get_user_by_email: MagicMock):
        """Test đăng ký user khi email đã tồn tại."""
        mock_get_user_by_email.return_value = User(id=1, email=test_user_email, password_hash="somehash") # Email đã tồn tại

        response = client.post(
            "/auth/register",
            json={"email": test_user_email, "password": "anypassword"},
        )
        
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Email already registered")
        mock_get_user_by_email.assert_called_once_with(unittest.mock.ANY, email=test_user_email)


    @patch("app.auth_service.api.crud_user.get_user_by_email")
    @patch("app.auth_service.api.verify_password")
    @patch("app.auth_service.api.create_access_token")
    def test_login_success(self, mock_create_access_token: MagicMock, mock_verify_password: MagicMock, mock_get_user_by_email: MagicMock):
        """Test đăng nhập thành công."""
        mock_user_db = User(id=1, email=test_user_email, password_hash=test_user_hashed_password)
        mock_get_user_by_email.return_value = mock_user_db
        mock_verify_password.return_value = True # Password đúng
        mock_create_access_token.return_value = "fake_access_token"

        response = client.post(
            "/auth/login",
            data={"username": test_user_email, "password": test_user_password}, # OAuth2PasswordRequestForm dùng form data
        )
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["access_token"], "fake_access_token")
        self.assertEqual(data["token_type"], "bearer")

        mock_get_user_by_email.assert_called_once_with(unittest.mock.ANY, email=test_user_email)
        mock_verify_password.assert_called_once_with(test_user_password, test_user_hashed_password)
        mock_create_access_token.assert_called_once()
        # Kiểm tra subject của create_access_token
        args_token, kwargs_token = mock_create_access_token.call_args
        self.assertEqual(kwargs_token['subject'], test_user_email)


    @patch("app.auth_service.api.crud_user.get_user_by_email")
    def test_login_user_not_found(self, mock_get_user_by_email: MagicMock):
        """Test đăng nhập khi user không tồn tại."""
        mock_get_user_by_email.return_value = None

        response = client.post(
            "/auth/login",
            data={"username": "nonexistent@example.com", "password": "anypassword"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Incorrect email or password")

    @patch("app.auth_service.api.crud_user.get_user_by_email")
    @patch("app.auth_service.api.verify_password")
    def test_login_incorrect_password(self, mock_verify_password: MagicMock, mock_get_user_by_email: MagicMock):
        """Test đăng nhập khi sai password."""
        mock_user_db = User(id=1, email=test_user_email, password_hash=test_user_hashed_password)
        mock_get_user_by_email.return_value = mock_user_db
        mock_verify_password.return_value = False # Password sai

        response = client.post(
            "/auth/login",
            data={"username": test_user_email, "password": "wrongpassword"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Incorrect email or password")

if __name__ == '__main__':
    unittest.main()