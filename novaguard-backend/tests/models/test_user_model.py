import unittest
from datetime import datetime

from app.models import User # Đảm bảo PYTHONPATH

class TestUserModel(unittest.TestCase):

    def test_user_creation_attributes(self):
        """Test basic User model attribute assignment (without DB interaction)."""
        user_data = {
            "email": "test@example.com",
            "password_hash": "hashed_password_example",
            "github_user_id": "gh_12345",
            "github_access_token_encrypted": "encrypted_token_example"
        }
        user = User(**user_data)

        self.assertEqual(user.email, user_data["email"])
        self.assertEqual(user.password_hash, user_data["password_hash"])
        self.assertEqual(user.github_user_id, user_data["github_user_id"])
        self.assertEqual(user.github_access_token_encrypted, user_data["github_access_token_encrypted"])
        
        # Timestamps are usually handled by DB or SQLAlchemy events,
        # so direct testing here might be limited without a session.
        # We can check if they exist.
        self.assertTrue(hasattr(user, "created_at"))
        self.assertTrue(hasattr(user, "updated_at"))

    def test_user_repr(self):
        """Test the __repr__ method of the User model."""
        user = User(id=1, email="repr_test@example.com", password_hash="dummy")
        self.assertEqual(repr(user), "<User(id=1, email='repr_test@example.com')>")

if __name__ == '__main__':
    unittest.main()