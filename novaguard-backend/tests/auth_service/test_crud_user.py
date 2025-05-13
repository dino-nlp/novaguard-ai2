import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy.orm import Session

from app.models import User
from app.auth_service.schemas import UserCreate
from app.auth_service import crud_user # Import module crud_user

# Giả định rằng app.core.security.get_password_hash đã tồn tại và hoạt động
# Chúng ta sẽ mock nó ở đây để test crud_user một cách độc lập
MOCKED_HASHED_PASSWORD = "mocked_hashed_password"

@patch("app.auth_service.crud_user.get_password_hash", return_value=MOCKED_HASHED_PASSWORD)
class TestUserCRUD(unittest.TestCase):

    def setUp(self):
        # Tạo một mock DB session cho mỗi test
        self.mock_db = MagicMock(spec=Session)

    def test_get_user_by_email_found(self, mock_get_password_hash):
        """Test lấy user bằng email khi user tồn tại."""
        expected_user = User(id=1, email="test@example.com", password_hash="somehash")
        
        # Cấu hình mock_db.query(...).filter(...).first() để trả về expected_user
        self.mock_db.query(User).filter(User.email == "test@example.com").first.return_value = expected_user
        
        user = crud_user.get_user_by_email(self.mock_db, email="test@example.com")
        
        self.mock_db.query(User).filter(User.email == "test@example.com").first.assert_called_once()
        self.assertEqual(user, expected_user)

    def test_get_user_by_email_not_found(self, mock_get_password_hash):
        """Test lấy user bằng email khi user không tồn tại."""
        self.mock_db.query(User).filter(User.email == "nonexistent@example.com").first.return_value = None
        
        user = crud_user.get_user_by_email(self.mock_db, email="nonexistent@example.com")
        
        self.mock_db.query(User).filter(User.email == "nonexistent@example.com").first.assert_called_once()
        self.assertIsNone(user)

    def test_get_user_by_id_found(self, mock_get_password_hash):
        """Test lấy user bằng ID khi user tồn tại."""
        expected_user = User(id=1, email="test@example.com", password_hash="somehash")
        self.mock_db.query(User).filter(User.id == 1).first.return_value = expected_user
        
        user = crud_user.get_user_by_id(self.mock_db, user_id=1)
        
        self.mock_db.query(User).filter(User.id == 1).first.assert_called_once()
        self.assertEqual(user, expected_user)

    def test_create_user(self, mock_get_password_hash: MagicMock):
        """Test tạo user mới."""
        user_in = UserCreate(email="newuser@example.com", password="password123")
        
        # db.add, db.commit, db.refresh không trả về giá trị nên không cần mock return_value
        # Chúng ta có thể kiểm tra xem chúng có được gọi không
        
        created_user = crud_user.create_user(self.mock_db, user_in)
        
        # Kiểm tra get_password_hash được gọi với password đúng
        mock_get_password_hash.assert_called_once_with("password123")
        
        # Kiểm tra các phương thức của session được gọi
        self.mock_db.add.assert_called_once()
        self.mock_db.commit.assert_called_once()
        self.mock_db.refresh.assert_called_once()
        
        # Kiểm tra user được tạo có email và password_hash đúng
        # created_user sẽ là argument được truyền vào self.mock_db.add
        added_user_arg = self.mock_db.add.call_args[0][0] # Lấy argument đầu tiên của lần gọi add
        self.assertIsInstance(added_user_arg, User)
        self.assertEqual(added_user_arg.email, user_in.email)
        self.assertEqual(added_user_arg.password_hash, MOCKED_HASHED_PASSWORD)
        
        # crud_user.create_user trả về đối tượng user đã được "refresh"
        # Trong môi trường mock, created_user chính là added_user_arg vì db.refresh được mock
        self.assertEqual(created_user, added_user_arg)

if __name__ == '__main__':
    unittest.main()