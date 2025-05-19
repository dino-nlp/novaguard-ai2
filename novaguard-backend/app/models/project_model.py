# novaguard-backend/app/models/project_model.py
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func, Enum as SQLAlchemyEnum, UniqueConstraint, Float
from sqlalchemy.orm import relationship
from app.core.db import Base
from app.models.full_project_analysis_request_model import FullProjectAnalysisStatus # Import enum
import enum

class LLMProviderEnum(str, enum.Enum):
    OLLAMA = "ollama"
    OPENAI = "openai"
    GEMINI = "gemini" # Hoặc "google" tùy bạn đặt tên

class OutputLanguageEnum(str, enum.Enum):
    ENGLISH = "en"
    VIETNAMESE = "vi"
    KOREAN = "ko"

class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    
    github_repo_id = Column(String(255), nullable=False)
    repo_name = Column(String(255), nullable=False)
    main_branch = Column(String(255), nullable=False)
    
    language = Column(String(100), nullable=True, comment="Primary programming language of the project code")
    custom_project_notes = Column(Text, nullable=True)
    github_webhook_id = Column(String(255), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), default=func.now(), onupdate=func.now())
    llm_provider = Column(SQLAlchemyEnum(LLMProviderEnum,
                                    name="project_llm_provider_enum", # Tên kiểu ENUM trong DB
                                    create_type=True, # Để SQLAlchemy tạo ENUM type này nếu chưa có
                                    values_callable=lambda obj: [e.value for e in obj]), 
                        nullable=True, server_default=LLMProviderEnum.OLLAMA.value) # Mặc định là ollama

    llm_model_name = Column(String(255), nullable=True, comment="Specific LLM model name, e.g., codellama:7b or gpt-3.5-turbo")
    llm_temperature = Column(Float, nullable=True, default=0.1, comment="LLM temperature setting for this project")
    llm_api_key_override_encrypted = Column(Text, nullable=True, comment="Encrypted API key to override global settings for this project")
    output_language = Column(SQLAlchemyEnum(OutputLanguageEnum,
                                        name="project_output_language_enum",
                                        create_type=True,
                                        values_callable=lambda obj: [e.value for e in obj]),
                            nullable=True, server_default=OutputLanguageEnum.ENGLISH.value)

    # Khóa ngoại trỏ đến bản ghi full scan cuối cùng
    last_full_scan_request_id = Column(Integer, ForeignKey("fullprojectanalysisrequests.id", ondelete="SET NULL"), nullable=True)
    # Kiểu ENUM này sử dụng lại type đã tạo bởi FullProjectAnalysisRequest, nên create_type=False là đúng
    last_full_scan_status = Column(SQLAlchemyEnum(FullProjectAnalysisStatus,
                                            name="full_project_analysis_status_enum",
                                            create_type=False,
                                            values_callable=lambda obj: [e.value for e in obj]),
                            nullable=True)
    last_full_scan_at = Column(DateTime(timezone=True), nullable=True)

    owner = relationship("User", back_populates="projects")
    pr_analysis_requests = relationship("PRAnalysisRequest", back_populates="project", cascade="all, delete-orphan")

    # Mối quan hệ để lấy tất cả các full scan requests cho project này
    full_scan_requests = relationship(
        "FullProjectAnalysisRequest",
        foreign_keys="FullProjectAnalysisRequest.project_id", # Chỉ định khóa ngoại trên bảng FullProjectAnalysisRequest
        back_populates="project",
        cascade="all, delete-orphan" # Nếu xóa Project thì xóa cả các full scan requests liên quan
    )

    # Optional: Nếu bạn muốn một relationship riêng để lấy chi tiết của last_full_scan_request_id
    # last_full_scan_detail = relationship(
    #     "FullProjectAnalysisRequest",
    #     foreign_keys=[last_full_scan_request_id],
    #     primaryjoin="Project.last_full_scan_request_id == FullProjectAnalysisRequest.id",
    #     uselist=False # Vì đây là one-to-one (hoặc one-to-zero)
    # )


    __table_args__ = (UniqueConstraint('user_id', 'github_repo_id', name='_user_repo_uc'),)

    def __repr__(self):
        return f"<Project(id={self.id}, name='{self.repo_name}', user_id={self.user_id})>"