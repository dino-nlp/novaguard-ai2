import unittest
from datetime import datetime

from app.models import Project # Đảm bảo PYTHONPATH

class TestProjectModel(unittest.TestCase):

    def test_project_creation_attributes(self):
        """Test basic Project model attribute assignment (without DB interaction)."""
        project_data = {
            "user_id": 1,
            "github_repo_id": "gh_project_123",
            "repo_name": "owner/my-cool-project",
            "main_branch": "develop",
            "language": "Python",
            "custom_project_notes": "This is a test project."
        }
        project = Project(**project_data)

        self.assertEqual(project.user_id, project_data["user_id"])
        self.assertEqual(project.github_repo_id, project_data["github_repo_id"])
        self.assertEqual(project.repo_name, project_data["repo_name"])
        self.assertEqual(project.main_branch, project_data["main_branch"])
        self.assertEqual(project.language, project_data["language"])
        self.assertEqual(project.custom_project_notes, project_data["custom_project_notes"])
        
        self.assertTrue(hasattr(project, "created_at"))
        self.assertTrue(hasattr(project, "updated_at"))
        self.assertTrue(hasattr(project, "github_webhook_id")) # Kiểm tra sự tồn tại của trường

    def test_project_repr(self):
        """Test the __repr__ method of the Project model."""
        project = Project(id=1, repo_name="owner/repo-repr", user_id=1)
        self.assertEqual(repr(project), "<Project(id=1, name='owner/repo-repr', user_id=1)>")

if __name__ == '__main__':
    unittest.main()