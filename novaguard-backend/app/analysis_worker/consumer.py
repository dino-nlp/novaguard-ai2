import json
import logging
import time
import re
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker  # Đảm bảo import Session
# from kafka import KafkaConsumer, KafkaError # KafkaConsumer, KafkaError được dùng trong hàm main_worker

from app.llm_service import (
    invoke_llm_analysis_chain,
    LLMProviderConfig,
    LLMServiceError
)

from app.core.config import get_settings, Settings
from app.core.db import SessionLocal as AppSessionLocal
from app.core.security import decrypt_data
from app.models import User, Project, PRAnalysisRequest, PRAnalysisStatus, AnalysisFinding
from app.webhook_service import crud_pr_analysis
from app.analysis_module import crud_finding, schemas_finding as am_schemas 
from app.common.github_client import GitHubAPIClient

# Import Pydantic models cho LLM output
from .llm_schemas import  LLMStructuredOutput

# --- Logging Setup ---
# logger đã được định nghĩa và cấu hình ở phần trước, sử dụng tên "AnalysisWorker"
logger = logging.getLogger("AnalysisWorker")
logger = logging.getLogger("AnalysisWorker")
if not logger.handlers: # Kiểm tra để tránh thêm handler nhiều lần nếu module được reload
    handler = logging.StreamHandler()
    # (Bạn có thể muốn dùng sys.stdout thay vì sys.stderr mặc định của StreamHandler)
    # import sys
    # handler = logging.StreamHandler(sys.stdout) 
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s') # Thêm funcName, lineno
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    # Đặt ở đây để thấy các debug logs:
    logger.setLevel(logging.DEBUG if get_settings().DEBUG else logging.INFO) # Hoặc luôn là DEBUG khi phát triển
    # logger.setLevel(logging.DEBUG) # Luôn DEBUG cho worker

# Đường dẫn đến thư mục prompts
PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"

# --- Database Session Management for Worker ---
_worker_db_session_factory: Optional[sessionmaker] = None

def initialize_worker_db_session_factory_if_needed():
    global _worker_db_session_factory
    if _worker_db_session_factory is None:
        try:
            _worker_db_session_factory = AppSessionLocal
            logger.info("Worker DB Session factory (AppSessionLocal) initialized/confirmed for analysis_worker.")
        except Exception as e:
            logger.exception(f"Failed to initialize Worker DB Session factory: {e}")
            raise RuntimeError("Worker DB Session factory initialization failed.") from e

def get_db_session_for_worker() -> Optional[Session]:
    if _worker_db_session_factory is None:
        logger.warning("Worker DB Session factory was not pre-initialized by main_worker. Attempting now.")
        initialize_worker_db_session_factory_if_needed()
        if _worker_db_session_factory is None:
            logger.error("Failed to initialize DB factory on demand for worker.")
            return None
    db: Optional[Session] = None
    try:
        db = _worker_db_session_factory()
        return db
    except Exception as e:
        logger.exception(f"Failed to create DB session for worker: {e}")
        if db:
            db.close()
        return None

# --- GitHub Data Fetching Logic ---
async def fetch_pr_data_from_github(
    gh_client: GitHubAPIClient,
    owner: str,
    repo_slug: str,
    pr_number: int,
    head_sha_from_webhook: Optional[str]
) -> Dict[str, Any]:
    logger.info(f"Fetching PR details for {owner}/{repo_slug} PR #{pr_number}...")
    pr_details = await gh_client.get_pull_request_details(owner, repo_slug, pr_number)
    if not pr_details:
        raise Exception(f"Failed to fetch PR details for {owner}/{repo_slug} PR #{pr_number} from GitHub.")
    
    actual_head_sha = pr_details.get("head", {}).get("sha")
    if not actual_head_sha:
        if head_sha_from_webhook:
            logger.warning(f"Could not get head SHA from PR details, using SHA from webhook: {head_sha_from_webhook}")
            actual_head_sha = head_sha_from_webhook
        else:
            raise Exception(f"Could not determine head SHA for PR {owner}/{repo_slug} #{pr_number}.")
    logger.info(f"Using head_sha: {actual_head_sha} for PR {owner}/{repo_slug} #{pr_number}")

    logger.info(f"Fetching PR diff for {owner}/{repo_slug} PR #{pr_number}...")
    pr_diff_content = await gh_client.get_pull_request_diff(owner, repo_slug, pr_number)
    if pr_diff_content is None:
        logger.warning(f"PR diff is None for {owner}/{repo_slug} PR #{pr_number}. Proceeding with empty diff.")
        pr_diff_content = ""

    logger.info(f"Fetching changed files for {owner}/{repo_slug} PR #{pr_number}...")
    changed_files_info = await gh_client.get_pull_request_files(owner, repo_slug, pr_number)
    if changed_files_info is None:
        logger.warning(f"Changed files list is None for {owner}/{repo_slug} PR #{pr_number}. Proceeding with empty list.")
        changed_files_info = []

    fetched_files_content: List[Dict[str, Any]] = []
    if changed_files_info:
        for file_info in changed_files_info:
            file_path = file_info.get("filename")
            status = file_info.get("status")
            if not file_path:
                logger.warning(f"File info missing filename: {file_info}")
                continue

            current_file_data = {
                "filename": file_path, "status": status,
                "patch": file_info.get("patch"), "content": None
            }
            if status == "removed":
                logger.debug(f"Skipping content fetch for removed file: {file_path}")
            elif status in ["added", "modified", "renamed", "copied"]:
                logger.debug(f"Fetching content for file: {file_path} (status: {status}) at ref {actual_head_sha}...")
                content = await gh_client.get_file_content(owner, repo_slug, file_path, ref=actual_head_sha)
                current_file_data["content"] = content if content is not None else "" # Ensure string
            else:
                 logger.info(f"Skipping file '{file_path}' with unhandled status '{status}' for content fetching.")
            fetched_files_content.append(current_file_data)
            
    return {
        "pr_metadata": pr_details, "head_sha": actual_head_sha,
        "pr_diff": pr_diff_content, "changed_files": fetched_files_content,
    }

# --- Analysis Orchestrator Logic (Internal to worker for MVP1) ---
def create_dynamic_project_context(
    raw_pr_data: Dict[str, Any], 
    project_model: Project,
    pr_model: PRAnalysisRequest
) -> Dict[str, Any]:
    logger.info(f"Creating dynamic project context for PRAnalysisRequest ID: {pr_model.id}")
    pr_metadata = raw_pr_data.get("pr_metadata", {})
    
    formatted_changed_files_str_list = []
    if raw_pr_data.get("changed_files"):
        for f_data in raw_pr_data["changed_files"]:
            if f_data.get("content"): 
                content_snippet = (f_data["content"] or "")[:4000] # Giới hạn content cho mỗi file
                if len(f_data["content"] or "") > 4000:
                    content_snippet += "\n... (content truncated due to length)"
                
                file_entry = (
                    f"File: {f_data['filename']}\n"
                    f"Status: {f_data['status']}\n"
                )
                # Consider if patch is too verbose for LLM context. May omit or summarize.
                # if f_data.get('patch'):
                #     patch_snippet = (f_data['patch'] or "")[:1000]
                #     if len(f_data['patch'] or "") > 1000:
                #         patch_snippet += "\n... (patch truncated)"
                #     file_entry += f"Patch (diff for this file):\n```diff\n{patch_snippet}\n```\n"
                file_entry += f"Full Content (or snippet):\n```\n{content_snippet}\n```\n---"
                formatted_changed_files_str_list.append(file_entry)
    
    context = {
        "pr_title": pr_metadata.get("title") or pr_model.pr_title or "N/A",
        "pr_description": pr_metadata.get("body") or "",
        "pr_author": pr_metadata.get("user", {}).get("login", "N/A"),
        "head_branch": pr_metadata.get("head", {}).get("ref", "N/A"),
        "base_branch": pr_metadata.get("base", {}).get("ref", "N/A"),
        "pr_diff_content": (raw_pr_data.get("pr_diff", "") or "")[:8000], # Giới hạn tổng diff
        "formatted_changed_files_with_content": "\n".join(formatted_changed_files_str_list) if formatted_changed_files_str_list else "No relevant file content available for analysis.",
        "project_language": project_model.language or "Undefined",
        "project_custom_notes": project_model.custom_project_notes or "No custom project notes provided.",
        "raw_pr_data_changed_files": raw_pr_data.get("changed_files", [])
    }
    logger.debug(f"Dynamic context created for PR ID {pr_model.id}. Title: {context['pr_title']}")
    return context

def load_prompt_template_str(template_name: str) -> str: # Đổi tên hàm để rõ là trả về string
    """Loads a prompt template string from the prompts directory."""
    prompt_file = PROMPT_DIR / template_name
    if not prompt_file.exists():
        logger.error(f"Prompt template file not found: {prompt_file}")
        raise FileNotFoundError(f"Prompt template {template_name} not found.")
    return prompt_file.read_text(encoding="utf-8")

async def run_code_analysis_agent_v1(
    dynamic_context: Dict[str, Any], # dynamic_context chứa tất cả các giá trị cần cho prompt
    settings_obj: Settings # settings object từ get_settings()
) -> List[am_schemas.AnalysisFindingCreate]:
    pr_title_for_log = dynamic_context.get('pr_title', 'N/A')
    logger.info(f"Worker: Running Code Analysis Agent for PR: {pr_title_for_log} using centralized LLMService.")

    try:
        # 1. Load Prompt Template String (giữ nguyên)
        prompt_template_str = load_prompt_template_str("deep_logic_bug_hunter_v1.md")

        # 2. Chuẩn bị payload cho prompt (dynamic_context đã chứa các giá trị này)
        #    `invoke_payload` là `dynamic_context` đã được chuẩn bị
        #    (Kiểm tra các key cần thiết đã có trong `dynamic_context` trước khi gọi)
        invoke_payload = {
            key: dynamic_context[key]
            for key in dynamic_context # Lọc ra các key cần thiết cho prompt nếu cần
            # Ví dụ: nếu prompt chỉ cần một subset các key từ dynamic_context
        }
        # IMPORTANT: dynamic_context cần phải chứa tất cả các placeholder mà prompt template mong đợi,
        # ngoại trừ `format_instructions` sẽ được llm_service xử lý.
        # Kiểm tra này có thể thực hiện ở đây hoặc trong llm_service.

        # 3. Tạo cấu hình LLM cho llm_service
        # Lấy provider và model mặc định từ settings
        # Trong tương lai, có thể lấy từ project-specific config
        current_llm_provider_config = LLMProviderConfig(
            provider_name=settings_obj.DEFAULT_LLM_PROVIDER,
            # model_name sẽ được llm_service xác định dựa trên provider và settings_obj.OPENAI_DEFAULT_MODEL etc.
            # Hoặc bạn có thể xác định model_name ở đây nếu muốn logic đó nằm trong worker:
            # model_name = (
            #     settings_obj.OPENAI_DEFAULT_MODEL if settings_obj.DEFAULT_LLM_PROVIDER == "openai"
            #     else settings_obj.GEMINI_DEFAULT_MODEL if settings_obj.DEFAULT_LLM_PROVIDER == "gemini"
            #     else settings_obj.OLLAMA_DEFAULT_MODEL
            # ),
            temperature=0.1, # Hoặc lấy từ settings_obj
            # api_key không cần truyền ở đây, llm_service sẽ tự lấy từ settings_obj
        )

        logger.info(f"Worker: Invoking LLMService with provider config: {current_llm_provider_config.provider_name}")

        # 4. Gọi LLM Service
        structured_llm_output: LLMStructuredOutput = await invoke_llm_analysis_chain(
            prompt_template_str=prompt_template_str,
            dynamic_context_values=invoke_payload, # Đây là dict các giá trị để điền vào prompt
            output_pydantic_model_class=LLMStructuredOutput, # Schema Pydantic cho output
            llm_provider_config=current_llm_provider_config,
            settings_obj=settings_obj # llm_service dùng để lấy API keys, default models
        )

        num_findings_from_llm = len(structured_llm_output.findings) if structured_llm_output and structured_llm_output.findings else 0
        logger.info(f"Worker: Received structured response from LLMService for PR: {pr_title_for_log}. Number of raw findings: {num_findings_from_llm}")

        # 5. Convert LLM findings to AnalysisFindingCreate schemas (logic này giữ nguyên)
        analysis_findings_to_create: List[am_schemas.AnalysisFindingCreate] = []
        if structured_llm_output and structured_llm_output.findings:
            for llm_finding in structured_llm_output.findings:
                # ... (logic trích xuất code snippet và tạo finding_for_db như cũ) ...
                # Ví dụ:
                code_snippet_text = None
                if llm_finding.file_path and llm_finding.line_start is not None:
                    original_file_content = None
                    raw_changed_files = dynamic_context.get("raw_pr_data_changed_files", []) # Đảm bảo key này có trong dynamic_context
                    for file_detail in raw_changed_files:
                        if file_detail.get("filename") == llm_finding.file_path:
                            original_file_content = file_detail.get("content")
                            break
                    if original_file_content:
                        lines = original_file_content.splitlines()

                        CONTEXT_LINES_BEFORE_AFTER = 5 # Số dòng ngữ cảnh trước và sau

                        # Xác định dòng bắt đầu và kết thúc của lỗi (1-based từ LLM)
                        error_line_start_1based = llm_finding.line_start
                        error_line_end_1based = llm_finding.line_end if llm_finding.line_end is not None and llm_finding.line_end >= error_line_start_1based else error_line_start_1based

                        # Tính toán phạm vi snippet bao gồm cả context (0-based cho slicing)
                        snippet_start_idx_0based = max(0, error_line_start_1based - 1 - CONTEXT_LINES_BEFORE_AFTER)
                        snippet_end_idx_0based = min(len(lines), error_line_end_1based + CONTEXT_LINES_BEFORE_AFTER) # slice sẽ không bao gồm dòng này, nên + CONTEXT_LINES_BEFORE_AFTER là đúng
        
                        if snippet_start_idx_0based < snippet_end_idx_0based:
                            snippet_lines_with_context = lines[snippet_start_idx_0based:snippet_end_idx_0based]

                            # Đánh dấu các dòng lỗi thực sự (tùy chọn, nếu muốn highlight trong frontend)
                            # Dòng lỗi bắt đầu trong snippet (0-based relative to snippet_lines_with_context)
                            error_start_in_snippet_0based = (error_line_start_1based - 1) - snippet_start_idx_0based
                            # Dòng lỗi kết thúc trong snippet (0-based relative to snippet_lines_with_context)
                            error_end_in_snippet_0based = (error_line_end_1based - 1) - snippet_start_idx_0based

                            # Thêm tiền tố hoặc class để frontend có thể highlight 
                            formatted_snippet_lines = []
                            for i, line_text in enumerate(snippet_lines_with_context):
                                actual_line_number = snippet_start_idx_0based + 1 + i
                                prefix = f"{actual_line_number:>{len(str(snippet_end_idx_0based))}} | " # Căn chỉnh số dòng
                                if error_start_in_snippet_0based <= i <= error_end_in_snippet_0based:
                                    prefix = f">{prefix}" # Đánh dấu dòng lỗi
                                else:
                                    prefix = f" {prefix}"
                                formatted_snippet_lines.append(prefix + line_text)
                            code_snippet_text = "\n".join(formatted_snippet_lines)

                            # Cách đơn giản hơn là chỉ join các dòng, frontend tự xử lý highlight nếu cần
                            # code_snippet_text = "\n".join(snippet_lines_with_context)

                        else:
                            logger.warning(f"Invalid line range for snippet extraction (with context): file '{llm_finding.file_path}', "
                                            f"LLM lines L{error_line_start_1based}-L{error_line_end_1based}, "
                                            f"calculated snippet slice [{snippet_start_idx_0based}:{snippet_end_idx_0based}] for {len(lines)} actual lines. PR: {pr_title_for_log}")
                            # Fallback về snippet gốc nếu có lỗi logic
                            start_idx_orig = max(0, error_line_start_1based - 1)
                            end_idx_orig = min(len(lines), error_line_end_1based)
                            if start_idx_orig < end_idx_orig :
                                code_snippet_text = "\n".join(lines[start_idx_orig:end_idx_orig])


                finding_for_db = am_schemas.AnalysisFindingCreate(
                    file_path=llm_finding.file_path,
                    line_start=llm_finding.line_start,
                    line_end=llm_finding.line_end,
                    severity=llm_finding.severity,
                    message=llm_finding.message,
                    suggestion=llm_finding.suggestion,
                    agent_name=f"NovaGuardAgent_v1_{current_llm_provider_config.provider_name}", # Tên agent có thể kèm provider
                    code_snippet=code_snippet_text
                )
                analysis_findings_to_create.append(finding_for_db)
        
        return analysis_findings_to_create

    except LLMServiceError as e_llm_service:
        logger.error(f"Worker: LLMServiceError during analysis for PR '{pr_title_for_log}': {e_llm_service}")
        # Lỗi này đã được log chi tiết bởi llm_service. Worker có thể chỉ cần ghi nhận và trả về rỗng.
        return [] # Trả về list rỗng nếu có lỗi từ LLM service
    except FileNotFoundError as e_fnf: # Lỗi không tìm thấy file prompt
        logger.error(f"Worker: Prompt file error for PR '{pr_title_for_log}': {e_fnf}")
        return []
    except KeyError as e_key: # Lỗi thiếu key trong dynamic_context cho prompt
        logger.error(f"Worker: KeyError formatting prompt for PR '{pr_title_for_log}': {e_key}. Check dynamic_context and prompt template.")
        return []
    except Exception as e: # Các lỗi không mong muốn khác trong worker
        logger.exception(f"Worker: Unexpected error during code analysis agent execution for PR '{pr_title_for_log}': {type(e).__name__} - {e}")
        return []

async def process_message_logic(message_value: dict, db: Session, settings_obj):
    pr_analysis_request_id = message_value.get("pr_analysis_request_id")
    if not pr_analysis_request_id: 
        logger.error("PML: Kafka message missing 'pr_analysis_request_id'. Skipping.")
        return

    logger.info(f"PML: Starting processing for PRAnalysisRequest ID: {pr_analysis_request_id}")
    db_pr_request = crud_pr_analysis.get_pr_analysis_request_by_id(db, pr_analysis_request_id)

    if not db_pr_request:
        logger.error(f"PML: PRAnalysisRequest ID {pr_analysis_request_id} not found in DB. Skipping.")
        return

    # Chỉ xử lý nếu đang PENDING, hoặc FAILED (để thử lại), hoặc DATA_FETCHED (để chạy lại LLM nếu cần)
    if db_pr_request.status not in [PRAnalysisStatus.PENDING, PRAnalysisStatus.FAILED, PRAnalysisStatus.DATA_FETCHED]:
        logger.info(f"PML: PR ID {pr_analysis_request_id} has status '{db_pr_request.status.value}', not processable now. Skipping.")
        return
    
    db_project = db.query(Project).filter(Project.id == db_pr_request.project_id).first()
    if not db_project:
        error_msg = f"Associated project (ID: {db_pr_request.project_id}) not found for PR ID {pr_analysis_request_id}."
        logger.error(error_msg)
        crud_pr_analysis.update_pr_analysis_request_status(db, pr_analysis_request_id, PRAnalysisStatus.FAILED, error_msg)
        return

    # Cập nhật status lên PROCESSING, xóa error_message cũ nếu có
    crud_pr_analysis.update_pr_analysis_request_status(db, pr_analysis_request_id, PRAnalysisStatus.PROCESSING, error_message=None)
    logger.info(f"PML: Updated PR ID {pr_analysis_request_id} to PROCESSING.")

    user_id = message_value.get("user_id")
    if not user_id:
        error_msg = f"User ID missing in Kafka message for PRAnalysisRequest {pr_analysis_request_id}."
        logger.error(error_msg)
        crud_pr_analysis.update_pr_analysis_request_status(db, pr_analysis_request_id, PRAnalysisStatus.FAILED, error_msg)
        return

    db_user = db.query(User).filter(User.id == user_id).first()
    if not db_user or not db_user.github_access_token_encrypted:
        error_msg = f"User (ID: {user_id}) not found or GitHub token missing for PR ID {pr_analysis_request_id}."
        logger.error(error_msg)
        crud_pr_analysis.update_pr_analysis_request_status(db, pr_analysis_request_id, PRAnalysisStatus.FAILED, error_msg)
        return
    
    github_token = decrypt_data(db_user.github_access_token_encrypted)
    if not github_token:
        error_msg = f"GitHub token decryption failed for user ID {user_id} (PR ID {pr_analysis_request_id})."
        logger.error(error_msg)
        crud_pr_analysis.update_pr_analysis_request_status(db, pr_analysis_request_id, PRAnalysisStatus.FAILED, error_msg)
        return
    
    gh_client = GitHubAPIClient(token=github_token)
    
    if '/' not in db_project.repo_name:
        error_msg = f"Invalid project repo_name format: {db_project.repo_name} for project ID {db_project.id}."
        logger.error(error_msg)
        crud_pr_analysis.update_pr_analysis_request_status(db, pr_analysis_request_id, PRAnalysisStatus.FAILED, error_msg)
        return
    owner, repo_slug = db_project.repo_name.split('/', 1)
    
    pr_number = db_pr_request.pr_number
    head_sha_from_webhook = message_value.get("head_sha", db_pr_request.head_sha)

    try:
        logger.info(f"PML: Fetching GitHub data for PR ID {pr_analysis_request_id}...")
        raw_pr_data = await fetch_pr_data_from_github(gh_client, owner, repo_slug, pr_number, head_sha_from_webhook)
        
        if raw_pr_data.get("pr_metadata"):
            # ... (cập nhật db_pr_request với metadata từ GitHub) ...
            pr_meta = raw_pr_data["pr_metadata"]
            db_pr_request.pr_title = pr_meta.get("title", db_pr_request.pr_title)
            html_url_val = pr_meta.get("html_url")
            db_pr_request.pr_github_url = str(html_url_val) if html_url_val else db_pr_request.pr_github_url
        db_pr_request.head_sha = raw_pr_data.get("head_sha", db_pr_request.head_sha)
        db.commit()
        db.refresh(db_pr_request)

        crud_pr_analysis.update_pr_analysis_request_status(db, pr_analysis_request_id, PRAnalysisStatus.DATA_FETCHED)
        logger.info(f"PML: PR ID {pr_analysis_request_id} status updated to DATA_FETCHED. Preparing for LLM analysis.")

        # Tạo dynamic_context
        dynamic_context = create_dynamic_project_context(raw_pr_data, db_project, db_pr_request)
        # QUAN TRỌNG: Thêm raw_pr_data['changed_files'] vào context để agent có thể dùng để trích xuất snippet
        dynamic_context["raw_pr_data_changed_files"] = raw_pr_data.get("changed_files", [])
        
        logger.info(f"PML: Invoking analysis agent via LLMService for PR ID {pr_analysis_request_id}...")
        
        # Gọi agent mới thay vì run_deep_logic_bug_hunter_mvp1 cũ
        analysis_findings_create_schemas: List[am_schemas.AnalysisFindingCreate] = await run_code_analysis_agent_v1(
            dynamic_context=dynamic_context,
            settings_obj=settings_obj
        )
        
        if analysis_findings_create_schemas:
            # crud_finding.create_analysis_findings nhận List[AnalysisFindingCreate]
            created_db_findings = crud_finding.create_analysis_findings(
                db, 
                pr_analysis_request_id, 
                analysis_findings_create_schemas # Đây là list các Pydantic model
            )
            logger.info(f"PML: Saved {len(created_db_findings)} findings from Langchain agent for PR ID {pr_analysis_request_id}.")
        else:
            logger.info(f"PML: Langchain agent returned no findings for PR ID {pr_analysis_request_id}.")

        crud_pr_analysis.update_pr_analysis_request_status(db, pr_analysis_request_id, PRAnalysisStatus.COMPLETED)
        logger.info(f"PML: PR ID {pr_analysis_request_id} analysis COMPLETED with Langchain agent.")

    except Exception as e:
        error_msg_detail = f"Error in process_message_logic for PR ID {pr_analysis_request_id} (Provider: {settings_obj.DEFAULT_LLM_PROVIDER}): {type(e).__name__} - {str(e)}"
        logger.exception(error_msg_detail)
        try:
            crud_pr_analysis.update_pr_analysis_request_status(db, pr_analysis_request_id, PRAnalysisStatus.FAILED, error_msg_detail[:1020])
        except Exception as db_error:
            logger.error(f"PML: Additionally, failed to update PR ID {pr_analysis_request_id} status to FAILED: {db_error}")


# --- Kafka Consumer Loop and Main Worker Function ---
async def consume_messages():
    settings_obj = get_settings() # Load settings một lần
    consumer = None
    
    # Kafka Connection Retry Logic
    max_retries = 5
    retry_delay = 10 # seconds
    for attempt in range(max_retries):
        try:
            from kafka import KafkaConsumer # Import ở đây để tránh lỗi nếu kafka-python chưa được cài khi load module
            from kafka.errors import KafkaError
            consumer = KafkaConsumer(
                settings_obj.KAFKA_PR_ANALYSIS_TOPIC,
                bootstrap_servers=settings_obj.KAFKA_BOOTSTRAP_SERVERS.split(','),
                auto_offset_reset='earliest',
                group_id='novaguard-analysis-workers-v5', # Thay đổi group_id nếu logic thay đổi đáng kể
                value_deserializer=lambda v: json.loads(v.decode('utf-8')),
                consumer_timeout_ms=10000 # Tăng timeout để worker có thời gian chờ message hơn
            )
            logger.info(f"KafkaConsumer connected to {settings_obj.KAFKA_BOOTSTRAP_SERVERS}, topic '{settings_obj.KAFKA_PR_ANALYSIS_TOPIC}'")
            break 
        except KafkaError as e:
            logger.warning(f"Attempt {attempt + 1}/{max_retries}: Kafka connection failed: {e}. Retrying in {retry_delay}s...")
            if attempt + 1 == max_retries:
                logger.error("Max retries reached for Kafka connection. Worker will exit.")
                return
            time.sleep(retry_delay)
    
    if not consumer: # Nếu không thể kết nối sau tất cả các lần thử
        return

    logger.info("Analysis worker started. Waiting for messages...")
    try:
        for message in consumer:
            logger.info(f"Consumed Kafka message: topic={message.topic}, partition={message.partition}, offset={message.offset}, key={message.key}")
            logger.debug(f"Message value raw: {message.value}")
            
            db_session = get_db_session_for_worker()
            if db_session:
                try:
                    # Truyền settings_obj vào đây vì nó chứa OLLAMA_DEFAULT_MODEL
                    await process_message_logic(message.value, db_session, settings_obj)
                except Exception as e_proc:
                    logger.exception(f"CRITICAL: Unhandled exception directly in process_message_logic for offset {message.offset}: {e_proc}")
                    # Cân nhắc việc không commit offset hoặc đưa vào dead-letter queue ở đây
                finally:
                    db_session.close()
                    logger.debug(f"DB session closed for offset {message.offset}")
            else:
                logger.error(f"Could not get DB session for processing message at offset {message.offset}. Message will likely be re-processed by another consumer instance if available, or after worker restarts and DB is up.")
                # Có thể cần một cơ chế retry hoặc dead-letter queue ở đây nếu DB thường xuyên không sẵn sàng
            
            # Nếu enable_auto_commit=False (mặc định là True cho kafka-python consumer),
            # bạn cần commit offset thủ công:
            # consumer.commit() 
            # logger.debug(f"Offset {message.offset} committed manually.")

    except KeyboardInterrupt:
        logger.info("Analysis worker shutting down (KeyboardInterrupt)...")
    except Exception as e:
        logger.exception(f"An unexpected error occurred in the main Kafka consumer loop: {e}")
    finally:
        if consumer:
            consumer.close()
            logger.info("KafkaConsumer closed.")

def main_worker():
    # Load settings sớm để đảm bảo các biến môi trường được đọc
    settings_obj = get_settings()
    logger.info(f"Initializing analysis worker with settings: OLLAMA_BASE_URL='{settings_obj.OLLAMA_BASE_URL}', DEFAULT_MODEL='{settings_obj.OLLAMA_DEFAULT_MODEL}'")
    
    # Khởi tạo DB session factory một lần khi worker bắt đầu
    try:
        initialize_worker_db_session_factory_if_needed()
    except RuntimeError as e:
        logger.critical(f"Failed to initialize worker due to DB session factory error: {e}. Worker cannot start.")
        return

    import asyncio
    try:
        asyncio.run(consume_messages())
    except Exception as e: # Bắt lỗi từ asyncio.run hoặc từ consume_messages nếu nó raise trước khi vào loop
        logger.critical(f"Analysis worker main_worker function exited with error: {e}", exc_info=True)

if __name__ == "__main__":
    # Cấu hình logging cơ bản nếu chạy trực tiếp script này
    # (Nhưng khi chạy qua `python -m app.analysis_worker.consumer`, logging đã được thiết lập ở trên)
    # if not logger.handlers:
    #     logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    main_worker()