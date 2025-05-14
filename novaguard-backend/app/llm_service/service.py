# novaguard-backend/app/llm_service/service.py
import logging
from typing import Type, Dict, Any, Optional, TypeVar

from pydantic import BaseModel

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.prompts import PromptTemplate, ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from langchain.output_parsers import OutputFixingParser
from langchain_core.exceptions import LangChainException

from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
# from langchain_google_vertexai import ChatVertexAI # Nếu muốn hỗ trợ Vertex AI

from app.core.config import Settings # Để truy cập API keys và default models
from .schemas import LLMProviderConfig, LLMServiceError # Các schema vừa định nghĩa

logger = logging.getLogger(__name__)

# Kiểu generic cho output Pydantic model
PydanticOutputModel = TypeVar('PydanticOutputModel', bound=BaseModel)

async def _get_configured_llm(
    llm_provider_config: LLMProviderConfig,
    settings_obj: Settings
) -> BaseChatModel:
    """
    Khởi tạo và trả về một instance LLM của Langchain dựa trên cấu hình.
    """
    provider = llm_provider_config.provider_name.lower()
    model_name_override = llm_provider_config.model_name
    temperature = llm_provider_config.temperature
    api_key_override = llm_provider_config.api_key
    additional_kwargs = llm_provider_config.additional_kwargs or {}

    logger.info(f"Attempting to initialize LLM provider: '{provider}', model_override: '{model_name_override}'")

    try:
        if provider == "openai":
            api_key = api_key_override or settings_obj.OPENAI_API_KEY
            if not api_key:
                raise LLMServiceError("OpenAI API key is not configured.", provider="openai")
            model_name = model_name_override or settings_obj.OPENAI_DEFAULT_MODEL
            logger.info(f"Initializing ChatOpenAI with model: {model_name}")
            return ChatOpenAI(
                openai_api_key=api_key,
                model_name=model_name,
                temperature=temperature,
                **additional_kwargs
            )
        elif provider == "gemini":
            # Langchain's ChatGoogleGenerativeAI sẽ tự tìm GOOGLE_API_KEY env var
            # hoặc sử dụng GOOGLE_APPLICATION_CREDENTIALS.
            # Nếu api_key_override được cung cấp, nó sẽ được ưu tiên.
            api_key_to_use = api_key_override or settings_obj.GOOGLE_API_KEY
            # if not api_key_to_use and not settings_obj.GOOGLE_APPLICATION_CREDENTIALS:
            #     logger.warning("Neither GOOGLE_API_KEY nor GOOGLE_APPLICATION_CREDENTIALS seems to be set for Gemini.")
                # Không raise lỗi ở đây, để Langchain thử tự tìm.

            model_name = model_name_override or settings_obj.GEMINI_DEFAULT_MODEL
            logger.info(f"Initializing ChatGoogleGenerativeAI with model: {model_name}")
            return ChatGoogleGenerativeAI(
                model=model_name,
                google_api_key=api_key_to_use, # Sẽ None nếu không được set, Langchain tự xử lý
                temperature=temperature,
                convert_system_message_to_human=True, # Thường cần cho Gemini
                **additional_kwargs
            )
        elif provider == "ollama":
            model_name = model_name_override or settings_obj.OLLAMA_DEFAULT_MODEL
            if not settings_obj.OLLAMA_BASE_URL:
                raise LLMServiceError("OLLAMA_BASE_URL is not configured.", provider="ollama")
            logger.info(f"Initializing ChatOllama with model: {model_name}, base_url: {settings_obj.OLLAMA_BASE_URL}")
            return ChatOllama(
                model=model_name,
                base_url=settings_obj.OLLAMA_BASE_URL,
                temperature=temperature,
                **additional_kwargs
            )
        else:
            raise LLMServiceError(f"Unsupported LLM provider: {provider}", provider=provider)
    except LangChainException as e:
        logger.error(f"LangChain specific error initializing {provider} LLM: {e}")
        raise LLMServiceError(f"Failed to initialize LLM for {provider}: {str(e)}", provider=provider, details=str(e)) from e
    except Exception as e:
        logger.exception(f"Unexpected error initializing {provider} LLM.")
        raise LLMServiceError(f"Unexpected error during LLM initialization for {provider}.", provider=provider, details=str(e)) from e

async def invoke_llm_analysis_chain(
    prompt_template_str: str,
    dynamic_context_values: Dict[str, Any],
    output_pydantic_model_class: Type[PydanticOutputModel],
    llm_provider_config: LLMProviderConfig,
    settings_obj: Settings
) -> PydanticOutputModel:
    """
    Hàm chính của LLM service.
    Nó nhận prompt, context, cấu hình LLM, và schema output Pydantic mong muốn.
    Nó tự xây dựng chain và trả về Pydantic object đã được parse.
    """
    provider_name_for_log = llm_provider_config.provider_name
    logger.info(f"LLMService: Invoking analysis chain with provider: {provider_name_for_log}")
    logger.debug(f"LLMService: Dynamic context keys: {list(dynamic_context_values.keys())}")
    logger.debug(f"LLMService: Output Pydantic class: {output_pydantic_model_class.__name__}")

    try:
        llm_instance = await _get_configured_llm(llm_provider_config, settings_obj)

        pydantic_parser = PydanticOutputParser(pydantic_object=output_pydantic_model_class)
        # Cân nhắc việc OutputFixingParser có thể làm tăng độ trễ và chi phí (vì gọi LLM thêm 1 lần để sửa lỗi)
        # Có thể làm cho nó tùy chọn.
        output_parser_with_fix = OutputFixingParser.from_llm(parser=pydantic_parser, llm=llm_instance)

        chat_prompt_template_obj = ChatPromptTemplate.from_template(template=prompt_template_str)

        # Partial fill format_instructions
        # Các biến khác trong dynamic_context_values sẽ được truyền khi invoke chain
        final_prompt_for_chain = chat_prompt_template_obj.partial(
            format_instructions=pydantic_parser.get_format_instructions()
        )

        # Xây dựng chain
        analysis_chain = final_prompt_for_chain | llm_instance | output_parser_with_fix

        # Log payload invoke (cẩn thận với dữ liệu lớn)
        if logger.isEnabledFor(logging.DEBUG):
            loggable_invoke_payload = {
                k: (str(v)[:200] + "..." if isinstance(v, str) and len(v) > 200 else v)
                for k, v in dynamic_context_values.items()
            }
            large_keys = ["formatted_changed_files_with_content", "pr_diff_content"]
            for key in large_keys:
                if key in loggable_invoke_payload: del loggable_invoke_payload[key]
            logger.debug(f"LLMService: Payload for chain.ainvoke (partial, excluding large content): {loggable_invoke_payload}")


        # Gọi chain
        # `dynamic_context_values` phải chứa tất cả các input_variables mà prompt template yêu cầu
        # (ngoại trừ `format_instructions` đã được partial fill)
        missing_vars_for_invoke = set(final_prompt_for_chain.input_variables) - set(dynamic_context_values.keys()) - {"format_instructions"}
        if missing_vars_for_invoke:
            logger.error(f"LLMService: Invoke payload missing variables: {missing_vars_for_invoke}. "
                        f"Prompt expects: {final_prompt_for_chain.input_variables}. "
                        f"Dynamic_context has keys: {list(dynamic_context_values.keys())}")
            raise LLMServiceError(
                f"LLM prompt is missing required variables: {missing_vars_for_invoke}",
                provider=provider_name_for_log
            )

        parsed_output: PydanticOutputModel = await analysis_chain.ainvoke(dynamic_context_values)
        
        logger.info(f"LLMService: Successfully received and parsed structured response from {provider_name_for_log}.")
        if logger.isEnabledFor(logging.DEBUG) and isinstance(parsed_output, BaseModel):
            try:
                logger.debug(f"LLMService: Parsed output (JSON):\n{parsed_output.model_dump_json(indent=2)}")
            except Exception: pass # Bỏ qua nếu không dump được

        return parsed_output

    except LLMServiceError: # Re-raise các lỗi đã được gói bởi _get_configured_llm hoặc lỗi payload
        raise
    except LangChainException as e:
        logger.error(f"LLMService: LangChain specific error during chain invocation with {provider_name_for_log}: {e}")
        # Phân tích thêm lỗi Langchain (ví dụ: AuthenticationError, RateLimitError) nếu cần
        raise LLMServiceError(
            f"LLM chain invocation failed for {provider_name_for_log}: {str(e)}",
            provider=provider_name_for_log,
            details=str(e) # hoặc e.args
        ) from e
    except Exception as e:
        logger.exception(f"LLMService: Unexpected error during LLM analysis chain invocation with {provider_name_for_log}.")
        raise LLMServiceError(
            f"An unexpected error occurred in the LLM service with {provider_name_for_log}.",
            provider=provider_name_for_log,
            details=str(e)
        ) from e