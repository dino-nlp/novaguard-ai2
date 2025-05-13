import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from sqlalchemy.orm import Session, sessionmaker  # Đảm bảo import Session
# from kafka import KafkaConsumer, KafkaError # KafkaConsumer, KafkaError được dùng trong hàm main_worker

from app.core.config import get_settings
from app.core.db import SessionLocal as AppSessionLocal
from app.core.security import decrypt_data
from app.models import User, Project, PRAnalysisRequest, PRAnalysisStatus, AnalysisFinding
from app.webhook_service import crud_pr_analysis # Để update status PRAnalysisRequest
from app.analysis_module import crud_finding, schemas_finding # CRUD và Schema cho Findings
from app.common.github_client import GitHubAPIClient
from app.llm_service import invoke_ollama, LLMServiceError

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

async def run_deep_logic_bug_hunter_mvp1(context: Dict[str, Any], settings_obj) -> List[Dict[str, Any]]:
    logger.info("Running DeepLogicBugHunterAI_MVP1 agent...")
    
    # --- Updated English Prompt ---
    prompt_template = f"""
You are an expert code reviewer focused on identifying potential logic errors, security vulnerabilities, and subtle issues in code.
Project Language: {context.get("project_language", "Undefined")}
Project-specific coding conventions or architectural notes (if any): {context.get("project_custom_notes", "None provided")}

Pull Request Information:
- Title: {context.get("pr_title", "N/A")}
- Description: {context.get("pr_description", "N/A")}
- Author: {context.get("pr_author", "N/A")}
- Branch: {context.get("head_branch", "N/A")} -> {context.get("base_branch", "N/A")}

Overall PR Diff (partial, if available):
```diff
{context.get("pr_diff_content", "No overall diff provided")}
```

Changed files and their content (or snippets):
{context.get("formatted_changed_files_with_content", "No changed file content available.")}

Based on ALL the provided information (PR description, overall diff, and full content of changed files), please perform a thorough analysis.
Your main goal is to find:
1. Potential logical errors (e.g., null pointer exceptions, incorrect condition handling, simple race conditions).
2. Edge cases that might lead to errors.
3. Code segments that could be improved in terms of logic, performance, or readability.
4. Basic data safety issues (e.g., leaking sensitive information in logs).

DO NOT comment on code style or minor issues that a linter can catch. Focus on MORE SIGNIFICANT and SUBTLE problems.

For EACH distinct issue you identify, provide the information STRICTLY as a JSON OBJECT within a JSON LIST.
Each JSON object MUST have the following fields:
- "file_path": (string) The full path of the relevant file.
- "line_start": (integer, optional) The starting line number of the relevant code segment in the file.
- "line_end": (integer, optional) The ending line number of the relevant code segment.
- "severity": (string) MUST be one of: "Error", "Warning", or "Note".
- "message": (string) A clear, detailed description of the issue.
- "suggestion": (string, optional) A suggestion on how to fix or improve the code.

Example of the desired JSON output format:
[
  {{"file_path": "src/moduleA.py", "line_start": 25, "line_end": 28, "severity": "Warning", "message": "The variable 'x' might be None at line 27, potentially leading to a NullPointerException if its 'value' attribute is accessed without a prior check.", "suggestion": "Consider adding an 'if x is not None:' check before accessing x.value."}},
  {{"file_path": "src/utils/calculator.js", "line_start": 102, "severity": "Error", "message": "The 'while(true)' loop at this location does not have a clear exit condition within its body, risking an infinite loop if internal logic doesn't guarantee a 'break'."}}
]
ONLY RETURN THE JSON LIST. Do NOT include any other explanatory text, greetings, or formatting outside of the JSON list itself. If no significant issues are found, return an empty JSON list: [].
    """
    
    logger.debug(f"Prompt for LLM (first 500 chars): {prompt_template[:500]}...")
    
    try:
        llm_response_text = await invoke_ollama(
            prompt=prompt_template, 
            model_name=settings_obj.OLLAMA_DEFAULT_MODEL
        )
        logger.info("Received response from LLM.")
        logger.debug(f"LLM raw response (first 500 chars): {llm_response_text[:500]}")

        findings = []
        try:
            json_start = llm_response_text.find('[')
            json_end = llm_response_text.rfind(']')
            if json_start != -1 and json_end != -1 and json_end >= json_start:
                json_str = llm_response_text[json_start : json_end+1]
                logger.debug(f"Attempting to parse JSON string from LLM: {json_str}")
                parsed_findings = json.loads(json_str)
                if isinstance(parsed_findings, list):
                    for pf_dict in parsed_findings:
                        if isinstance(pf_dict, dict) and \
                           all(k in pf_dict for k in ["file_path", "severity", "message"]) and \
                           isinstance(pf_dict["file_path"], str) and \
                           isinstance(pf_dict["severity"], str) and pf_dict["severity"] in ["Error", "Warning", "Note"] and \
                           isinstance(pf_dict["message"], str):
                            line_start = pf_dict.get("line_start")
                            line_end = pf_dict.get("line_end")
                            if line_start is not None and not isinstance(line_start, int): line_start = None
                            if line_end is not None and not isinstance(line_end, int): line_end = None
                            
                            findings.append({
                                "file_path": pf_dict["file_path"],
                                "line_start": line_start, "line_end": line_end,
                                "severity": pf_dict["severity"], "message": pf_dict["message"],
                                "suggestion": pf_dict.get("suggestion") if isinstance(pf_dict.get("suggestion"), str) else None,
                                "agent_name": "DeepLogicBugHunterAI_MVP1"
                            })
                        else:
                            logger.warning(f"Skipping invalid finding structure from LLM: {pf_dict}")
                else:
                    logger.error(f"LLM response was valid JSON but not a list. Type: {type(parsed_findings)}. Content: {str(parsed_findings)[:500]}")
            else:
                logger.error(f"Could not find valid JSON list structure in LLM response. Snippet: {llm_response_text[:500]}")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {e}. Response snippet: {llm_response_text[:500]}")
        except Exception as e_parse:
            logger.exception(f"Unexpected error parsing LLM findings: {e_parse}. Response snippet: {llm_response_text[:500]}")

        logger.info(f"Parsed {len(findings)} findings from LLM.")
        return findings
        
    except LLMServiceError as e_llm:
        logger.error(f"LLMServiceError in DeepLogicBugHunterAI: {e_llm.message}, Details: {e_llm.details}")
        raise
    except Exception as e_unexpected:
        logger.exception("Unexpected error in DeepLogicBugHunterAI.")
        raise

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
            pr_meta = raw_pr_data["pr_metadata"]
            db_pr_request.pr_title = pr_meta.get("title", db_pr_request.pr_title)
            html_url_val = pr_meta.get("html_url")
            db_pr_request.pr_github_url = str(html_url_val) if html_url_val else db_pr_request.pr_github_url
        db_pr_request.head_sha = raw_pr_data.get("head_sha", db_pr_request.head_sha)
        db.commit()
        db.refresh(db_pr_request) 

        crud_pr_analysis.update_pr_analysis_request_status(db, pr_analysis_request_id, PRAnalysisStatus.DATA_FETCHED)
        logger.info(f"PML: PR ID {pr_analysis_request_id} status updated to DATA_FETCHED. Preparing for LLM analysis.")

        dynamic_context = create_dynamic_project_context(raw_pr_data, db_project, db_pr_request)
        
        logger.info(f"PML: Invoking LLM for PR ID {pr_analysis_request_id}...")
        llm_findings_dicts = await run_deep_logic_bug_hunter_mvp1(dynamic_context, settings_obj)
        
        if llm_findings_dicts:
            findings_to_create_schemas = []
            for finding_dict in llm_findings_dicts:
                try:
                    # Validate và tạo schema object trước khi tạo DB object
                    findings_to_create_schemas.append(schemas_finding.AnalysisFindingCreate(**finding_dict))
                except Exception as e_pydantic: # Bắt lỗi validation của Pydantic
                    logger.warning(f"PML: Invalid finding structure from LLM, skipping: {finding_dict}. Error: {e_pydantic}")
            
            if findings_to_create_schemas:
                created_db_findings = crud_finding.create_analysis_findings(db, pr_analysis_request_id, findings_to_create_schemas)
                logger.info(f"PML: Saved {len(created_db_findings)} findings from LLM for PR ID {pr_analysis_request_id}.")
            else:
                logger.info(f"PML: No valid findings to save after LLM processing for PR ID {pr_analysis_request_id}.")
        else:
            logger.info(f"PML: LLM returned no findings for PR ID {pr_analysis_request_id}.")

        crud_pr_analysis.update_pr_analysis_request_status(db, pr_analysis_request_id, PRAnalysisStatus.COMPLETED)
        logger.info(f"PML: PR ID {pr_analysis_request_id} analysis COMPLETED.")

    except Exception as e:
        error_msg_detail = f"Error in process_message_logic for PR ID {pr_analysis_request_id}: {type(e).__name__} - {str(e)}"
        logger.exception(error_msg_detail) # Log full traceback để dễ debug
        try:
            crud_pr_analysis.update_pr_analysis_request_status(db, pr_analysis_request_id, PRAnalysisStatus.FAILED, error_msg_detail[:1020]) # Giới hạn độ dài
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