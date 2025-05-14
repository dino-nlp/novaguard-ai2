from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError # Để bắt lỗi UniqueConstraint
from typing import List, Optional

from app.models import Project
from app.project_service.schemas import ProjectCreate, ProjectUpdate

def create_project(db: Session, project_in: ProjectCreate, user_id: int) -> Project | None:
    """
    Tạo một project mới cho một user.
    Trả về Project nếu thành công, None nếu có lỗi (ví dụ: vi phạm unique constraint).
    """
    db_project = Project(
        **project_in.model_dump(), # Lấy tất cả dữ liệu từ Pydantic model
        user_id=user_id
    )
    db.add(db_project)
    try:
        db.commit()
        db.refresh(db_project)
        return db_project
    except IntegrityError: # Bắt lỗi nếu user_id và github_repo_id đã tồn tại
        db.rollback()
        return None 

def get_project_by_id(db: Session, project_id: int, user_id: int) -> Project | None:
    """
    Lấy một project theo ID, đảm bảo project đó thuộc về user_id được cung cấp.
    """
    return db.query(Project).filter(Project.id == project_id, Project.user_id == user_id).first()

def get_projects_by_user(db: Session, user_id: int, skip: int = 0, limit: int = 100) -> List[Project]:
    """
    Lấy danh sách các project của một user.
    """
    return db.query(Project).filter(Project.user_id == user_id).offset(skip).limit(limit).all()

def update_project(
    db: Session, 
    project_id: int, 
    project_in: ProjectUpdate, 
    user_id: int
) -> Project | None:
    """
    Cập nhật một project. Chỉ user sở hữu mới có thể cập nhật.
    """
    db_project = get_project_by_id(db=db, project_id=project_id, user_id=user_id)
    if not db_project:
        return None

    update_data = project_in.model_dump(exclude_unset=True) # Chỉ lấy các trường được cung cấp để update
    for key, value in update_data.items():
        setattr(db_project, key, value)
    
    db.add(db_project) # Hoặc db.merge(db_project)
    db.commit()
    db.refresh(db_project)
    return db_project

def delete_project(db: Session, project_id: int, user_id: int) -> Project | None:
    """
    Xóa một project. Chỉ user sở hữu mới có thể xóa.
    """
    db_project = get_project_by_id(db=db, project_id=project_id, user_id=user_id)
    if not db_project:
        return None
    
    db.delete(db_project)
    db.commit()
    return db_project # Trả về project đã xóa (trước khi bị xóa khỏi session)

# def get_project_by_github_repo_id_first(db: Session, github_repo_id: str) -> Project | None:
# """
# Lấy project đầu tiên tìm thấy dựa trên github_repo_id.
# Cần cẩn thận nếu nhiều user có thể thêm cùng một repo.
# """
# return db.query(Project).filter(Project.github_repo_id == github_repo_id).first()
# -> Thay vì hàm này, logic trong webhook_api.py đã query join với User rồi.

def delete_project(db: Session, project_id: int, user_id: int) -> Project | None:
    """
    Xóa một project. Chỉ user sở hữu mới có thể xóa.
    Trả về project đã xóa (trước khi bị xóa khỏi session) nếu thành công, ngược lại None.
    """
    # get_project_by_id đã kiểm tra user_id sở hữu project
    db_project = get_project_by_id(db=db, project_id=project_id, user_id=user_id)
    if not db_project:
        return None
    
    # Lưu lại thông tin cần thiết TRƯỚC KHI xóa khỏi DB
    # (vì sau khi commit, db_project có thể không còn truy cập được một số thuộc tính)
    # Tuy nhiên, hàm này nên trả về db_project như hiện tại,
    # logic gọi GitHub API sẽ xử lý việc lấy thông tin trước.
    
    db.delete(db_project)
    db.commit()
    return db_project # Trả về project đã bị xóa (trước khi commit, nó vẫn còn trong session)