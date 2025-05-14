import json
import logging
import time
import re
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker  # Đảm bảo import Session
# from kafka import KafkaConsumer, KafkaError # KafkaConsumer, KafkaError được dùng trong hàm main_worker

# Langchain imports
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import PydanticOutputParser, OutputFixingParser # Thêm OutputFixingParser
from langchain_ollama import ChatOllama

from app.core.config import get_settings
from app.core.db import SessionLocal as AppSessionLocal
from app.core.security import decrypt_data
from app.models import User, Project, PRAnalysisRequest, PRAnalysisStatus, AnalysisFinding
from app.webhook_service import crud_pr_analysis
from app.analysis_module import crud_finding, schemas_finding as am_schemas 
from app.common.github_client import GitHubAPIClient

# Import Pydantic models cho LLM output
from .llm_schemas import LLMSingleFinding, LLMStructuredOutput

# --- Logging Setup ---
# logger đã được định nghĩa và cấu hình ở phần trước, sử dụng tên "AnalysisWorker"
logger = logging.getLogger("AnalysisWorker")
# Nếu bạn muốn chắc chắn handler được thêm chỉ một lần (ví dụ khi module được nạp lại trong 1 số kịch bản test)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO) # Hoặc DEBUG nếu cần chi tiết hơn
# ---

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
        "project_custom_notes": project_model.custom_project_notes or "No custom project notes provided."
    }
    logger.debug(f"Dynamic context created for PR ID {pr_model.id}. Title: {context['pr_title']}")
    return context

def load_prompt_template(template_name: str) -> str:
    """Loads a prompt template from the prompts directory."""
    prompt_file = PROMPT_DIR / template_name
    if not prompt_file.exists():
        logger.error(f"Prompt template file not found: {prompt_file}")
        raise FileNotFoundError(f"Prompt template {template_name} not found.")
    return prompt_file.read_text(encoding="utf-8")

async def run_code_analysis_agent_v1(
    dynamic_context: Dict[str, Any], # dynamic_context từ create_dynamic_project_context
    settings_obj: Any # settings object từ get_settings()
) -> List[am_schemas.AnalysisFindingCreate]: # Trả về list các schema để tạo finding trong DB
    """
    Runs the DeepLogicBugHunterAI agent using Langchain.
    """
    logger.info(f"Running Langchain-based Code Analysis Agent for PR: {dynamic_context.get('pr_title', 'N/A')}")

    try:
        # 1. Load Prompt Template
        prompt_content = load_prompt_template("deep_logic_bug_hunter_v1.md")

        # 2. Initialize LLM
        # Bạn có thể dùng OllamaLLM (cho completion) hoặc ChatOllama (cho chat models)
        # OllamaLLM có vẻ phù hợp hơn nếu prompt của bạn là một template text lớn.
        llm = ChatOllama(
            model=settings_obj.OLLAMA_DEFAULT_MODEL,
            base_url=settings_obj.OLLAMA_BASE_URL,
            temperature=0.2, # Điều chỉnh nhiệt độ để kết quả nhất quán hơn
            # num_ctx=4096, # Tùy chỉnh context window nếu cần và model hỗ trợ
        )

        # 3. Setup Output Parser
        # Parser sẽ cố gắng parse output của LLM thành LLMStructuredOutput Pydantic model.
        pydantic_parser = PydanticOutputParser(pydantic_object=LLMStructuredOutput)
        
        # (Tùy chọn nhưng khuyến khích) OutputFixingParser để thử sửa lỗi JSON nếu LLM trả về không hoàn hảo
        # Cần LLM để sửa lỗi, nên nó sẽ gọi LLM thêm một lần nếu parsing lỗi.
        output_fixing_parser = OutputFixingParser.from_llm(parser=pydantic_parser, llm=llm)


        # 4. Create PromptTemplate object
        # Nó sẽ tự động lấy format_instructions từ parser.
        prompt = PromptTemplate(
            template=prompt_content,
            input_variables=list(dynamic_context.keys()), # Các key trong dynamic_context
            partial_variables={"format_instructions": pydantic_parser.get_format_instructions()}
        )
        logger.debug(f"Prompt to be sent (with format instructions):\n{prompt.template}")


        # 5. Create Langchain Chain (LCEL - Langchain Expression Language)
        # Chuỗi: format prompt -> gọi LLM -> parse output
        chain = prompt | llm | output_fixing_parser # Sử dụng output_fixing_parser

        # 6. Invoke Chain
        logger.info(f"Invoking Langchain analysis chain for PR: {dynamic_context.get('pr_title', 'N/A')}")
        # Truyền các giá trị thực tế từ dynamic_context vào chain
        # Ví dụ: chain.invoke({"pr_title": "...", "project_language": "...", ...})
        llm_response_structured: LLMStructuredOutput = await chain.ainvoke(dynamic_context)
        
        logger.info(f"Received structured response from Langchain chain for PR: {dynamic_context.get('pr_title', 'N/A')}. Number of findings: {len(llm_response_structured.findings)}")

        # 7. Convert LLM findings to AnalysisFindingCreate schemas
        analysis_findings_to_create: List[am_schemas.AnalysisFindingCreate] = []
        if llm_response_structured and llm_response_structured.findings:
            for llm_finding in llm_response_structured.findings:
                # Trích xuất code snippet (logic này giữ nguyên hoặc cải tiến)
                code_snippet_text = None
                if llm_finding.file_path and llm_finding.line_start is not None:
                    original_file_content = None
                    # `dynamic_context` cần chứa `raw_pr_data_changed_files`
                    # raw_pr_data_changed_files = dynamic_context.get("raw_pr_data_changed_files", [])
                    # Đây là một thay đổi quan trọng: làm sao để có raw_pr_data_changed_files ở đây một cách hiệu quả.
                    # Giả sử nó được truyền vào dynamic_context
                    
                    # Để truy cập file content, dynamic_context cần có thông tin này.
                    # Giả sử dynamic_context["raw_pr_data_changed_files"] là list các dict {"filename": "path", "content": "text"}
                    raw_changed_files = dynamic_context.get("raw_pr_data_changed_files", [])
                    for file_detail in raw_changed_files:
                        if file_detail.get("filename") == llm_finding.file_path:
                            original_file_content = file_detail.get("content")
                            break
                    
                    if original_file_content:
                        lines = original_file_content.splitlines()
                        actual_end_line = llm_finding.line_end if llm_finding.line_end is not None else llm_finding.line_start
                        start_idx = max(0, llm_finding.line_start - 1)
                        end_idx = min(len(lines), actual_end_line)
                        if start_idx < end_idx:
                            snippet_lines = lines[start_idx:end_idx]
                            code_snippet_text = "\n".join(snippet_lines)
                        else:
                            logger.warning(f"Invalid line range for snippet: file {llm_finding.file_path}, L{llm_finding.line_start}-L{actual_end_line}")
                    else:
                        logger.warning(f"Could not find content for file {llm_finding.file_path} to extract snippet.")

                # Map severity từ string (Error, Warning, Note) sang Enum nếu schema DB/Pydantic dùng Enum
                # Ví dụ, nếu am_schemas.AnalysisFindingCreate.severity là Enum:
                # severity_enum_val = am_schemas.SeverityLevel[llm_finding.severity.upper()] 
                # (cần định nghĩa SeverityLevel Enum trong am_schemas)
                # Nếu schema vẫn dùng string thì không cần chuyển.
                # Schema hiện tại của bạn (AnalysisFindingBase) dùng `severity: str`.
                
                finding_for_db = am_schemas.AnalysisFindingCreate(
                    file_path=llm_finding.file_path,
                    line_start=llm_finding.line_start,
                    line_end=llm_finding.line_end,
                    severity=llm_finding.severity, # Giữ là string nếu schema là string
                    message=llm_finding.message,
                    suggestion=llm_finding.suggestion,
                    agent_name="DeepLogicBugHunterAI_AgentV1_LC", # Cập nhật tên agent
                    code_snippet=code_snippet_text
                )
                analysis_findings_to_create.append(finding_for_db)
        
        return analysis_findings_to_create

    except FileNotFoundError as e_fnf:
        logger.error(f"Prompt file error: {e_fnf}")
        # Có thể raise lại hoặc trả về list rỗng tùy chiến lược xử lý lỗi
        return []
    except Exception as e:
        logger.exception(f"Error during Langchain code analysis for PR {dynamic_context.get('pr_title', 'N/A')}: {e}")
        # Có thể raise lại hoặc trả về list rỗng
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
        
        logger.info(f"PML: Invoking Langchain-based analysis agent for PR ID {pr_analysis_request_id}...")
        
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
        error_msg_detail = f"Error in process_message_logic (Langchain) for PR ID {pr_analysis_request_id}: {type(e).__name__} - {str(e)}"
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