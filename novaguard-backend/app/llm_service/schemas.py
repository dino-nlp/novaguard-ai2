# novaguard-backend/app/llm_service/schemas.py
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any

class LLMProviderConfig(BaseModel):
    """
    Cấu hình chi tiết cho một LLM provider cụ thể để truyền cho llm_service.
    """
    provider_name: str = Field(..., description="Tên nhà cung cấp: 'ollama', 'openai', 'gemini'")
    model_name: Optional[str] = Field(None, description="Tên model cụ thể (sẽ lấy default từ settings nếu None)")
    temperature: float = Field(0.1, ge=0.0, le=2.0)
    # Thêm các tham số Langchain phổ biến khác nếu cần
    # ví dụ: max_tokens, top_p
    additional_kwargs: Optional[Dict[str, Any]] = Field(None, description="Các tham số bổ sung cho provider cụ thể")
    # API key có thể được llm_service tự lấy từ settings, hoặc truyền vào đây nếu cần ghi đè
    api_key: Optional[str] = Field(None, description="API key (nếu cần ghi đè settings)")

class LLMServiceError(Exception):
    """Custom exception for LLM service errors."""
    def __init__(self, message, status_code=None, details=None, provider: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.details = details
        self.provider = provider

    def __str__(self):
        return f"LLMServiceError (Provider: {self.provider}): {self.args[0]} (Status: {self.status_code}, Details: {self.details})"