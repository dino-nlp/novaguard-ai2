import httpx
import logging
import json # Để parse lỗi JSON từ Ollama nếu có

from app.core.config import settings

logger = logging.getLogger(__name__)

class LLMServiceError(Exception):
    """Custom exception for LLM service errors."""
    def __init__(self, message, status_code=None, details=None):
        super().__init__(message)
        self.status_code = status_code
        self.details = details

async def invoke_ollama(
    prompt: str, 
    model_name: str | None = None, # Sẽ lấy default từ settings
    temperature: float = 0.7, # Thêm các tham số Ollama phổ biến
    top_p: float = 1.0,
    stream: bool = False # Mặc định không stream cho việc parse dễ hơn
) -> str:
    """
    Gửi một prompt đến Ollama API và trả về response text.
    """
    if not settings.OLLAMA_BASE_URL:
        logger.error("OLLAMA_BASE_URL is not configured.")
        raise LLMServiceError("Ollama service URL is not configured.")

    # Sử dụng OLLAMA_DEFAULT_MODEL từ settings nếu model_name không được cung cấp
    # Cần thêm OLLAMA_DEFAULT_MODEL="codellama:7b-instruct-q4_K_M" vào config.py và .env
    active_model_name = model_name or settings.OLLAMA_DEFAULT_MODEL
    if not active_model_name:
        logger.error("Ollama model name not provided and no default is set.")
        raise LLMServiceError("Ollama model name not specified.")

    ollama_api_url = f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/generate"

    payload = {
        "model": active_model_name,
        "prompt": prompt,
        "stream": stream, # Đặt False để nhận toàn bộ response một lần
        "options": { # Thêm các options nếu cần
            "temperature": temperature,
            "top_p": top_p,
            # "num_ctx": 4096 # Ví dụ: context window size
        }
    }
    # Loại bỏ options nếu không có giá trị cụ thể được truyền vào
    if temperature == 0.7 and top_p == 1.0: # Giá trị mặc định của Ollama
         del payload["options"]


    logger.info(f"Sending prompt to Ollama model: {active_model_name} at {ollama_api_url}")
    logger.debug(f"Ollama payload (prompt's first 100 chars): {{'model': '{active_model_name}', 'prompt': '{prompt[:100]}...', 'stream': {stream}}}")

    async with httpx.AsyncClient(timeout=120.0) as client: # Tăng timeout cho LLM
        try:
            response = await client.post(ollama_api_url, json=payload)
            response.raise_for_status() # Raise HTTPStatusError cho 4xx/5xx

            response_data = response.json()
            llm_response_text = response_data.get("response")

            if llm_response_text is None:
                logger.error(f"Ollama response missing 'response' field. Full response: {response_data}")
                raise LLMServiceError("Invalid response structure from Ollama: 'response' field missing.")

            logger.info(f"Received response from Ollama model {active_model_name}.")
            logger.debug(f"Ollama raw response snippet: {llm_response_text[:200]}...")
            return llm_response_text.strip()

        except httpx.HTTPStatusError as e:
            error_body = ""
            try:
                error_body = e.response.json()
            except json.JSONDecodeError:
                error_body = e.response.text
            logger.error(f"Ollama API Error: {e.response.status_code} - {e.request.url} - Response: {error_body}")
            raise LLMServiceError(f"Ollama API request failed with status {e.response.status_code}",
                                status_code=e.response.status_code, details=error_body) from e
        except httpx.RequestError as e:
            logger.error(f"Ollama API Request Error: {e.request.url} - {e}")
            raise LLMServiceError(f"Could not connect to Ollama service at {ollama_api_url}") from e
        except Exception as e:
            logger.exception(f"Unexpected error invoking Ollama for model {active_model_name}")
            raise LLMServiceError("Unexpected error during Ollama invocation") from e