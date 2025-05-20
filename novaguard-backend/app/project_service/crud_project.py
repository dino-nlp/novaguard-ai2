from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError # Để bắt lỗi UniqueConstraint
from typing import List, Optional
import logging 

from app.models import Project
from app.models.project_model import LLMProviderEnum, OutputLanguageEnum
from app.project_service.schemas import ProjectCreate, ProjectUpdate
from app.core.security import encrypt_data, decrypt_data # Cần encrypt_data
from app.core.config import settings # Có thể cần để lấy giá trị mặc định nếu schema không cung cấp

logger = logging.getLogger(__name__) # Khởi tạo logger


def create_project(db: Session, project_in: ProjectCreate, user_id: int) -> Project | None:
    encrypted_api_key_override = None
    if project_in.llm_api_key_override:
        encrypted_api_key_override = encrypt_data(project_in.llm_api_key_override)
        if not encrypted_api_key_override:
            logger.error(f"Failed to encrypt llm_api_key_override for project {project_in.repo_name}. Key not saved.")

    # Xử lý llm_provider: nếu là None, lấy từ settings.DEFAULT_LLM_PROVIDER
    final_llm_provider = project_in.llm_provider
    if final_llm_provider is None: # Nếu Pydantic schema cho phép None
        try:
            final_llm_provider = LLMProviderEnum(settings.DEFAULT_LLM_PROVIDER)
        except ValueError:
            logger.warning(f"Invalid DEFAULT_LLM_PROVIDER '{settings.DEFAULT_LLM_PROVIDER}' in settings. Defaulting to OLLAMA for project.")
            final_llm_provider = LLMProviderEnum.OLLAMA
    # Đảm bảo final_llm_provider là Enum member
    elif not isinstance(final_llm_provider, LLMProviderEnum):
        try: # Cố gắng convert string value sang Enum member
            final_llm_provider = LLMProviderEnum(str(final_llm_provider).lower())
        except ValueError:
            logger.warning(f"Invalid llm_provider value '{project_in.llm_provider}'. Defaulting to OLLAMA.")
            final_llm_provider = LLMProviderEnum.OLLAMA


    # Xử lý llm_model_name mặc định
    final_llm_model_name = project_in.llm_model_name
    if not final_llm_model_name: # None hoặc chuỗi rỗng (đã được Pydantic validator xử lý thành None)
        # Sử dụng final_llm_provider đã được xác định ở trên
        if final_llm_provider == LLMProviderEnum.OLLAMA:
            final_llm_model_name = settings.OLLAMA_DEFAULT_MODEL
        elif final_llm_provider == LLMProviderEnum.OPENAI:
            final_llm_model_name = settings.OPENAI_DEFAULT_MODEL
        elif final_llm_provider == LLMProviderEnum.GEMINI:
            final_llm_model_name = settings.GEMINI_DEFAULT_MODEL
        # Không cần else ở đây vì final_llm_provider đã được đảm bảo có giá trị Enum hợp lệ
        logger.info(f"No LLM model name provided for project {project_in.repo_name}. Using default: {final_llm_model_name} for provider {final_llm_provider.value}")


    # Xử lý output_language: nếu là None, lấy từ settings.DEFAULT_OUTPUT_LANGUAGE
    final_output_language = project_in.output_language
    if final_output_language is None: # Nếu Pydantic schema cho phép None
        try:
            final_output_language = OutputLanguageEnum(getattr(settings, 'DEFAULT_OUTPUT_LANGUAGE', 'en'))
        except ValueError:
            logger.warning(f"Invalid DEFAULT_OUTPUT_LANGUAGE in settings. Defaulting to ENGLISH for project.")
            final_output_language = OutputLanguageEnum.ENGLISH
    elif not isinstance(final_output_language, OutputLanguageEnum):
        try:
            final_output_language = OutputLanguageEnum(str(final_output_language).lower())
        except ValueError:
            logger.warning(f"Invalid output_language value '{project_in.output_language}'. Defaulting to ENGLISH.")
            final_output_language = OutputLanguageEnum.ENGLISH


    # Loại trừ các trường đã xử lý riêng khỏi model_dump
    db_project_data_to_unpack = project_in.model_dump(exclude={
        "llm_api_key_override", "llm_provider", "llm_model_name", "output_language", "llm_temperature"
    })

    db_project = Project(
        **db_project_data_to_unpack,
        user_id=user_id,
        llm_provider=final_llm_provider,             # Gán Enum member đã xử lý
        llm_model_name=final_llm_model_name,         # Gán model name đã xử lý
        output_language=final_output_language,       # Gán Enum member đã xử lý
        llm_api_key_override_encrypted=encrypted_api_key_override,
        # llm_temperature sẽ được lấy từ project_in.llm_temperature (đã có default trong Pydantic schema)
        # hoặc bạn có thể xử lý default ở đây nếu Pydantic không gửi (nhưng nên để Pydantic xử lý default)
        llm_temperature=project_in.llm_temperature # Giữ nguyên từ project_in
    )
    
    db.add(db_project)
    try:
        db.commit()
        db.refresh(db_project)
        logger.info(f"XXXXXXXXXX Project '{db_project.repo_name}' (ID: {db_project.id}) created successfully for user {user_id} "
                    f"with LLM provider: {db_project.llm_provider.value if db_project.llm_provider else 'N/A'}, "
                    f"model: {db_project.llm_model_name or 'Default'}, "
                    f"output lang: {db_project.output_language.value if db_project.output_language else 'N/A'}.")
        return db_project
    # ... (exception handling giữ nguyên) ...
    except IntegrityError:
        db.rollback()
        logger.error(f"IntegrityError creating project {project_in.repo_name} for user {user_id}.", exc_info=True)
        return None
    except Exception as e:
        db.rollback()
        logger.error(f"Exception creating project {project_in.repo_name} for user {user_id}: {e}", exc_info=True)
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
    return db.query(Project).filter(Project.user_id == user_id).order_by(Project.created_at.desc()).offset(skip).limit(limit).all()


def update_project(
    db: Session, 
    project_id: int, 
    project_in: ProjectUpdate, 
    user_id: int
) -> Project | None:
    """
    Cập nhật một project. Chỉ user sở hữu mới có thể cập nhật.
    Xử lý các trường cấu hình LLM và ngôn ngữ output mới.
    """
    db_project = get_project_by_id(db=db, project_id=project_id, user_id=user_id)
    if not db_project:
        return None

    update_data = project_in.model_dump(exclude_unset=True, exclude={"llm_api_key_override"}) # Không lấy các trường không được set
                                                                    # và loại trừ llm_api_key_override ban đầu

    for key, value in update_data.items():
        if key == "llm_model_name" and value == "": # Xử lý trường hợp model name rỗng -> None
            setattr(db_project, key, None)
        else:
            setattr(db_project, key, value) # value ở đây có thể là Enum member nếu Pydantic xử lý đúng

    # Xử lý llm_api_key_override riêng
    if project_in.llm_api_key_override is not None: # Có giá trị được gửi (kể cả chuỗi rỗng)
        if project_in.llm_api_key_override == "": # User muốn xóa key override
            db_project.llm_api_key_override_encrypted = None
            logger.info(f"Cleared llm_api_key_override for project ID {project_id}.")
        else: # User cung cấp key mới
            new_encrypted_key = encrypt_data(project_in.llm_api_key_override)
            if new_encrypted_key:
                db_project.llm_api_key_override_encrypted = new_encrypted_key
                logger.info(f"Updated and encrypted llm_api_key_override for project ID {project_id}.")
            else:
                logger.error(f"Failed to encrypt new llm_api_key_override for project ID {project_id}. Key not changed.")
    
    db.add(db_project) # Hoặc không cần add nếu db_project đã được tracked
    try:
        db.commit()
        db.refresh(db_project)
        logger.info(f"Project ID {project_id} updated successfully.")
        return db_project
    except Exception as e:
        db.rollback()
        logger.error(f"Exception updating project ID {project_id}: {e}", exc_info=True)
        return None

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
