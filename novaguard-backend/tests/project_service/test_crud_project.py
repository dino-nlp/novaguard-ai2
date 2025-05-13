import unittest
from unittest.mock import MagicMock, patch
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.models import Project
from app.project_service.schemas import ProjectCreate, ProjectUpdate
from app.project_service import crud_project

class TestProjectCRUD(unittest.TestCase):

    def setUp(self):
        self.mock_db = MagicMock(spec=Session)
        self.test_user_id = 1

    def test_create_project_success(self):
        project_in = ProjectCreate(
            github_repo_id="gh_crud_1",
            repo_name="owner/crud_test",
            main_branch="main"
        )
        
        # Giả lập hành vi của DB session
        # db.add, db.commit, db.refresh không trả về giá trị cụ thể mà ta cần kiểm tra ở đây
        # thay vào đó ta kiểm tra đối số truyền vào db.add
        
        created_project = crud_project.create_project(self.mock_db, project_in, self.test_user_id)

        self.mock_db.add.assert_called_once()
        added_project_arg = self.mock_db.add.call_args[0][0]
        self.assertIsInstance(added_project_arg, Project)
        self.assertEqual(added_project_arg.github_repo_id, project_in.github_repo_id)
        self.assertEqual(added_project_arg.repo_name, project_in.repo_name)
        self.assertEqual(added_project_arg.main_branch, project_in.main_branch)
        self.assertEqual(added_project_arg.user_id, self.test_user_id)
        
        self.mock_db.commit.assert_called_once()
        self.mock_db.refresh.assert_called_once_with(added_project_arg)
        self.assertEqual(created_project, added_project_arg) # create_project trả về đối tượng đã refresh

    def test_create_project_integrity_error(self):
        project_in = ProjectCreate(
            github_repo_id="gh_crud_conflict",
            repo_name="owner/conflict_test",
            main_branch="main"
        )
        # Giả lập db.commit() raise IntegrityError
        self.mock_db.commit.side_effect = IntegrityError("mocked integrity error", params=None, orig=None)

        created_project = crud_project.create_project(self.mock_db, project_in, self.test_user_id)

        self.mock_db.add.assert_called_once()
        self.mock_db.commit.assert_called_once()
        self.mock_db.rollback.assert_called_once() # Quan trọng: phải rollback khi có lỗi
        self.mock_db.refresh.assert_not_called() # Không refresh nếu lỗi
        self.assertIsNone(created_project)


    def test_get_project_by_id_found(self):
        expected_project = Project(id=1, repo_name="owner/found", user_id=self.test_user_id)
        self.mock_db.query(Project).filter().first.return_value = expected_project
        
        project = crud_project.get_project_by_id(self.mock_db, project_id=1, user_id=self.test_user_id)
        
        self.mock_db.query(Project).filter().first.assert_called_once()
        # (Có thể kiểm tra các điều kiện filter nếu cần, nhưng mock chung là đủ cho unit test này)
        self.assertEqual(project, expected_project)

    def test_get_project_by_id_not_found_or_wrong_user(self):
        self.mock_db.query(Project).filter().first.return_value = None
        
        project = crud_project.get_project_by_id(self.mock_db, project_id=99, user_id=self.test_user_id)
        self.assertIsNone(project)

        project_other_user = crud_project.get_project_by_id(self.mock_db, project_id=1, user_id=999) # user_id khác
        self.assertIsNone(project_other_user)


    def test_get_projects_by_user(self):
        expected_projects = [
            Project(id=1, repo_name="p1", user_id=self.test_user_id),
            Project(id=2, repo_name="p2", user_id=self.test_user_id)
        ]
        self.mock_db.query(Project).filter().offset(0).limit(100).all.return_value = expected_projects
        
        projects = crud_project.get_projects_by_user(self.mock_db, user_id=self.test_user_id, skip=0, limit=100)
        
        self.mock_db.query(Project).filter().offset(0).limit(100).all.assert_called_once()
        self.assertEqual(projects, expected_projects)
        self.assertEqual(len(projects), 2)

    def test_update_project_success(self):
        project_to_update = Project(id=1, repo_name="old_name", main_branch="old_branch", user_id=self.test_user_id, language="Python")
        # Giả lập get_project_by_id trả về project này
        with patch("app.project_service.crud_project.get_project_by_id", return_value=project_to_update) as mock_get:
            update_data = ProjectUpdate(repo_name="new_name", language="JavaScript")
            
            updated_project = crud_project.update_project(self.mock_db, project_id=1, project_in=update_data, user_id=self.test_user_id)
            
            mock_get.assert_called_once_with(db=self.mock_db, project_id=1, user_id=self.test_user_id)
            
            self.assertEqual(project_to_update.repo_name, "new_name") # Kiểm tra object đã được thay đổi
            self.assertEqual(project_to_update.language, "JavaScript")
            self.assertEqual(project_to_update.main_branch, "old_branch") # Trường không update giữ nguyên

            self.mock_db.add.assert_called_once_with(project_to_update)
            self.mock_db.commit.assert_called_once()
            self.mock_db.refresh.assert_called_once_with(project_to_update)
            self.assertEqual(updated_project, project_to_update)

    def test_update_project_not_found(self):
        with patch("app.project_service.crud_project.get_project_by_id", return_value=None) as mock_get:
            update_data = ProjectUpdate(repo_name="new_name")
            updated_project = crud_project.update_project(self.mock_db, project_id=99, project_in=update_data, user_id=self.test_user_id)
            
            mock_get.assert_called_once_with(db=self.mock_db, project_id=99, user_id=self.test_user_id)
            self.assertIsNone(updated_project)
            self.mock_db.add.assert_not_called()

    def test_delete_project_success(self):
        project_to_delete = Project(id=1, repo_name="to_delete", user_id=self.test_user_id)
        with patch("app.project_service.crud_project.get_project_by_id", return_value=project_to_delete) as mock_get:
            
            deleted_project = crud_project.delete_project(self.mock_db, project_id=1, user_id=self.test_user_id)
            
            mock_get.assert_called_once_with(db=self.mock_db, project_id=1, user_id=self.test_user_id)
            self.mock_db.delete.assert_called_once_with(project_to_delete)
            self.mock_db.commit.assert_called_once()
            self.assertEqual(deleted_project, project_to_delete)

    def test_delete_project_not_found(self):
        with patch("app.project_service.crud_project.get_project_by_id", return_value=None) as mock_get:
            deleted_project = crud_project.delete_project(self.mock_db, project_id=99, user_id=self.test_user_id)
            
            mock_get.assert_called_once_with(db=self.mock_db, project_id=99, user_id=self.test_user_id)
            self.assertIsNone(deleted_project)
            self.mock_db.delete.assert_not_called()

if __name__ == '__main__':
    unittest.main()