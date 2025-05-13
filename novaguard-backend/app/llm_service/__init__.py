# novaguard-backend/app/llm_service/__init__.py
from .wrapper import invoke_ollama, LLMServiceError

__all__ = ["invoke_ollama", "LLMServiceError"]