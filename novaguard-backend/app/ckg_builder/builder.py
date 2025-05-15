# novaguard-ai2/novaguard-backend/app/ckg_builder/builder.py
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
from neo4j import AsyncDriver # Thêm Async

from app.core.graph_db import get_async_neo4j_driver # Sử dụng driver đã tạo
from app.models import Project # SQLAlchemy model để lấy project_id, language
from .parsers import get_code_parser, ParsedFileResult, ExtractedFunction, ExtractedClass, ExtractedImport

logger = logging.getLogger(__name__)

class CKGBuilder:
    # ... (__init__, _get_driver, _execute_write_queries, _ensure_project_node, _clear_existing_data_for_file giữ nguyên) ...
    def __init__(self, project_model: Project, neo4j_driver: Optional[AsyncDriver] = None):
        self.project = project_model
        self.project_graph_id = f"novaguard_project_{project_model.id}" # ID của project trong graph
        self.file_path_to_node_id_cache: Dict[str, int] = {} # Cache để tránh query lại File node ID
        self._driver = neo4j_driver

    async def _get_driver(self) -> AsyncDriver:
        if self._driver and hasattr(self._driver, '_closed') and not self._driver._closed:
            return self._driver
        driver = await get_async_neo4j_driver()
        if not driver or (hasattr(driver, '_closed') and driver._closed):
            raise ConnectionError("Neo4j driver is not available or closed for CKGBuilder.")
        self._driver = driver
        return self._driver

    async def _execute_write_queries(self, queries_with_params: List[Tuple[str, Dict[str, Any]]]):
        if not queries_with_params: return
        driver = await self._get_driver()
        # Sử dụng database được cấu hình trong settings hoặc mặc định 'neo4j'
        db_name = getattr(driver, 'database', 'neo4j') # Lấy database name từ driver nếu có, fallback
        async with driver.session(database=db_name) as session:
            async with session.begin_transaction() as tx:
                for query, params in queries_with_params:
                    try:
                        # logger.debug(f"CKG Executing Cypher: {query} with params: {params}")
                        await tx.run(query, params)
                    except Exception as e:
                        logger.error(f"CKGBuilder: Error running Cypher: {query[:150]}... with params {params}. Error: {e}", exc_info=True)
                        await tx.rollback()
                        raise
                await tx.commit()
    
    async def _ensure_project_node(self):
        query = """
        MERGE (p:Project {graph_id: $project_graph_id})
        ON CREATE SET p.name = $repo_name, p.novaguard_id = $novaguard_project_id, p.language = $language
        ON MATCH SET p.name = $repo_name, p.language = $language
        """
        params = {
            "project_graph_id": self.project_graph_id,
            "repo_name": self.project.repo_name,
            "novaguard_project_id": self.project.id,
            "language": self.project.language
        }
        await self._execute_write_queries([(query, params)])
        logger.info(f"CKGBuilder: Ensured Project node '{self.project_graph_id}' exists/updated.")

    async def _clear_existing_data_for_file(self, file_path_in_repo: str):
        # Xóa các node Function, Class, và File (cùng relationships của chúng) cho file này
        # Xóa relationships CALLS trỏ đến hoặc đi từ các function/method trong file này
        queries = [
            (
                """
                MATCH (f:File {path: $file_path, project_graph_id: $project_graph_id})
                OPTIONAL MATCH (f)<-[:DEFINED_IN]-(entity) // Functions or Classes in this file
                OPTIONAL MATCH (entity)-[r_calls:CALLS]->() // Calls từ entity này
                OPTIONAL MATCH (entity)<-[r_called_by:CALLS]-() // Calls đến entity này
                DETACH DELETE f, entity // Xóa file, entities và tất cả relationships của chúng
                // Cần đảm bảo không xóa nhầm call target/source ở file khác nếu chỉ DETACH DELETE entity
                // Cách an toàn hơn là xóa từng loại relationship trước nếu cần
                """,
                # Query trên cần test kỹ. DETACH DELETE trên f và entity sẽ xóa luôn :DEFINED_IN.
                # CALLS relationships sẽ bị xóa nếu một trong hai đầu (entity) bị xóa.
                {"file_path": file_path_in_repo, "project_graph_id": self.project_graph_id}
            )
        ]
        logger.info(f"CKGBuilder: Clearing existing CKG data for file '{file_path_in_repo}' in project '{self.project_graph_id}'")
        await self._execute_write_queries(queries)


    async def process_file_for_ckg(self, file_path_in_repo: str, file_content: str, language: Optional[str]):
        if not language: # Ngôn ngữ phải được xác định
            logger.debug(f"CKGBuilder: Language not specified for file {file_path_in_repo}, skipping CKG.")
            return

        parser = get_code_parser(language)
        if not parser:
            logger.warning(f"CKGBuilder: No parser for lang '{language}' (file: {file_path_in_repo}). Skipping CKG.")
            return

        logger.info(f"CKGBuilder: Processing CKG for '{file_path_in_repo}' (lang: {language}), project: '{self.project_graph_id}'.")
        parsed_data: Optional[ParsedFileResult] = parser.parse(file_content, file_path_in_repo)

        if not parsed_data:
            logger.error(f"CKGBuilder: Failed to parse file {file_path_in_repo} for CKG.")
            return

        # Xóa dữ liệu CKG cũ của file này trước khi thêm mới
        await self._clear_existing_data_for_file(file_path_in_repo)

        cypher_batch: List[Tuple[str, Dict[str, Any]]] = []

        # 1. File Node
        file_node_props = {
            "path": file_path_in_repo,
            "project_graph_id": self.project_graph_id,
            "language": language,
            "name": Path(file_path_in_repo).name
        }
        cypher_batch.append((
            "MERGE (p:Project {graph_id: $project_graph_id}) "
            "MERGE (f:File {path: $path, project_graph_id: $project_graph_id}) "
            "ON CREATE SET f += $props "
            "ON MATCH SET f += $props "
            "MERGE (f)-[:PART_OF_PROJECT]->(p)",
            {"project_graph_id": self.project_graph_id, "path": file_path_in_repo, "props": file_node_props}
        ))

        # 2. Import Nodes and Relationships
        for imp_data in parsed_data.imports:
            if imp_data.module_path:
                # Module Node (đại diện cho module được import)
                # project_graph_id được thêm vào Module để phân biệt module cùng tên ở các project (nếu cần)
                # hoặc có thể để Module là global. Tạm thời để là project-specific.
                cypher_batch.append((
                    """
                    MERGE (m:Module {path: $module_path, project_graph_id: $project_graph_id})
                    ON CREATE SET m.name = last(split($module_path, '.'))
                    WITH m
                    MATCH (f:File {path: $file_path, project_graph_id: $project_graph_id})
                    MERGE (f)-[r:IMPORTS_MODULE]->(m)
                    SET r.type = $import_type
                    """,
                    {
                        "module_path": imp_data.module_path, "project_graph_id": self.project_graph_id,
                        "file_path": file_path_in_repo, "import_type": imp_data.import_type
                    }
                ))
                if imp_data.import_type == "from" and imp_data.imported_names:
                    for name, alias in imp_data.imported_names:
                        # Relationship IMPORTS_NAME_FROM_MODULE (hoặc property trên IMPORTS_MODULE)
                        # Đây là một ví dụ, bạn có thể muốn cấu trúc khác
                        cypher_batch.append((
                            """
                            MATCH (f:File {path: $file_path, project_graph_id: $project_graph_id})
                            MATCH (m:Module {path: $module_path, project_graph_id: $project_graph_id})
                            MERGE (f)-[r:IMPORTS_NAME_FROM_MODULE {original_name: $name}]->(m)
                            SET r.alias = $alias
                            """,
                            {
                                "file_path": file_path_in_repo, "module_path": imp_data.module_path,
                                "name": name, "alias": alias, "project_graph_id": self.project_graph_id
                            }
                        ))


        # 3. Class Nodes, Methods, and Inheritance
        for cls_data in parsed_data.classes:
            class_props = {
                "name": cls_data.name, "file_path": file_path_in_repo,
                "start_line": cls_data.start_line, "end_line": cls_data.end_line,
                "project_graph_id": self.project_graph_id
            }
            cypher_batch.append((
                """
                MATCH (f:File {path: $file_path, project_graph_id: $project_graph_id})
                MERGE (c:Class {name: $name, file_path: $file_path, start_line: $start_line, project_graph_id: $project_graph_id})
                ON CREATE SET c += $props_on_create
                ON MATCH SET c.end_line = $props_on_create.end_line
                MERGE (c)-[:DEFINED_IN]->(f)
                """,
                {"file_path": file_path_in_repo, "project_graph_id": self.project_graph_id,
                 "name": cls_data.name, "start_line": cls_data.start_line,
                 "props_on_create": class_props} # Truyền toàn bộ props để set khi CREATE
            ))

            for super_name in cls_data.superclasses:
                cypher_batch.append((
                    """
                    MATCH (this_class:Class {name: $class_name, file_path: $file_path, project_graph_id: $project_graph_id})
                    MERGE (super_class:Class {name: $super_name, project_graph_id: $project_graph_id}) // Giả định superclass cùng project, cần resolve sau
                    ON CREATE SET super_class.placeholder = true // Đánh dấu nếu nó chưa được parse từ file khác
                    MERGE (this_class)-[:INHERITS_FROM]->(super_class)
                    """,
                    {"class_name": cls_data.name, "file_path": file_path_in_repo,
                     "super_name": super_name, "project_graph_id": self.project_graph_id}
                ))

            for method_data in cls_data.methods:
                method_props = {
                    "name": method_data.name, "file_path": file_path_in_repo,
                    "start_line": method_data.start_line, "end_line": method_data.end_line,
                    "signature": method_data.signature, "parameters": method_data.parameters_str,
                    "class_name": cls_data.name, "project_graph_id": self.project_graph_id
                }
                cypher_batch.append((
                    """
                    MATCH (f:File {path: $file_path, project_graph_id: $project_graph_id})
                    MATCH (cls:Class {name: $class_name, file_path: $file_path, project_graph_id: $project_graph_id})
                    MERGE (m:Method:Function { // Sử dụng cả hai label
                        name: $name, file_path: $file_path, start_line: $start_line, project_graph_id: $project_graph_id
                    })
                    ON CREATE SET m += $props
                    ON MATCH SET m.end_line = $props.end_line, m.signature = $props.signature, m.parameters = $props.parameters
                    MERGE (m)-[:DEFINED_IN]->(f)
                    MERGE (m)-[:DEFINED_IN_CLASS]->(cls)
                    """,
                    {"file_path": file_path_in_repo, "project_graph_id": self.project_graph_id,
                     "class_name": cls_data.name, **method_props} # Merge method_props vào params
                ))

        # 4. Global Function Nodes
        for func_data in parsed_data.functions: # Global functions
            func_props = {
                "name": func_data.name, "file_path": file_path_in_repo,
                "start_line": func_data.start_line, "end_line": func_data.end_line,
                "signature": func_data.signature, "parameters": func_data.parameters_str,
                "project_graph_id": self.project_graph_id
            }
            cypher_batch.append((
                """
                MATCH (f:File {path: $file_path, project_graph_id: $project_graph_id})
                MERGE (fn:Function {
                    name: $name, file_path: $file_path, start_line: $start_line, project_graph_id: $project_graph_id
                })
                ON CREATE SET fn += $props
                ON MATCH SET fn.end_line = $props.end_line, fn.signature = $props.signature, fn.parameters = $props.parameters
                MERGE (fn)-[:DEFINED_IN]->(f)
                """,
                {"file_path": file_path_in_repo, "project_graph_id": self.project_graph_id, **func_props}
            ))
        
        # Thực thi batch tạo node và define relationships
        if cypher_batch:
            logger.debug(f"CKGBuilder: Executing initial batch of {len(cypher_batch)} Cypher queries for file {file_path_in_repo}.")
            await self._execute_write_queries(cypher_batch)

        # 5. Link CALLS (Sau khi tất cả functions/methods đã được MERGE)
        call_link_queries_batch: List[Tuple[str, Dict[str, Any]]] = []
        all_defined_entities_in_file = parsed_data.functions + [m for c in parsed_data.classes for m in c.methods]

        for defined_entity in all_defined_entities_in_file:
            if not defined_entity.calls: continue
            
            caller_label = ":Method:Function" if defined_entity.class_name else ":Function"
            
            for called_name, base_object_name, call_type in defined_entity.calls:
                # Đây là logic rất cơ bản, cần cải thiện với resolution engine
                # Cố gắng tìm callee trong cùng project, ưu tiên cùng file
                call_params = {
                    "caller_name": defined_entity.name, "caller_file_path": file_path_in_repo,
                    "caller_start_line": defined_entity.start_line,
                    "callee_name": called_name, "project_graph_id": self.project_graph_id
                }
                # Query tìm callee có thể rất phức tạp
                # MVP: tìm Function/Method có tên đó trong CÙNG FILE trước, sau đó là CÙNG PROJECT
                # Query này có thể tạo nhiều mối quan hệ CALLS nếu có nhiều function/method cùng tên
                # Cần một cơ chế "resolve" tốt hơn.
                resolve_query = f"""
                MATCH (caller{caller_label} {{
                    name: $caller_name, file_path: $caller_file_path, 
                    start_line: $caller_start_line, project_graph_id: $project_graph_id
                }})
                // Ưu tiên tìm trong cùng file
                OPTIONAL MATCH (callee_same_file:Function {{name: $callee_name, file_path: $caller_file_path, project_graph_id: $project_graph_id}})
                WITH caller, callee_same_file
                // Nếu không tìm thấy trong cùng file, tìm trong toàn bộ project
                OPTIONAL MATCH (callee_any_file:Function {{name: $callee_name, project_graph_id: $caller.project_graph_id}})
                WHERE callee_same_file IS NULL // Chỉ tìm nếu không thấy ở same_file
                
                WITH caller, COALESCE(callee_same_file, callee_any_file) AS callee
                WHERE callee IS NOT NULL // Chỉ tạo relationship nếu tìm thấy callee
                MERGE (caller)-[r:CALLS {{type: $call_type, base_object: $base_object_name }}]->(callee)
                """
                call_params_full = {**call_params, "call_type": call_type, "base_object_name": base_object_name}
                call_link_queries_batch.append((resolve_query, call_params_full))

        if call_link_queries_batch:
            logger.debug(f"CKGBuilder: Executing batch of {len(call_link_queries_batch)} CALLS link queries for file {file_path_in_repo}.")
            await self._execute_write_queries(call_link_queries_batch)
        
        logger.info(f"CKGBuilder: Finished CKG processing for file '{file_path_in_repo}'.")

    async def build_for_project_from_path(self, repo_local_path: str):
        # ... (logic duyệt file và gọi process_file_for_ckg như lần trước, đảm bảo await self._ensure_project_node()) ...
        logger.info(f"CKGBuilder: Starting CKG build for project '{self.project_graph_id}' from path: {repo_local_path}")
        await self._ensure_project_node()

        source_path_obj = Path(repo_local_path)
        files_processed_count = 0
        files_to_process: List[Tuple[Path, Optional[str]]] = []

        project_main_language = self.project.language.lower().strip() if self.project.language else None
        logger.info(f"CKGBuilder: Project main language hint: {project_main_language}")

        common_code_extensions = {
            '.py': 'python', '.js': 'javascript', '.jsx': 'javascript',
            '.ts': 'typescript', '.tsx': 'typescript',
            '.java': 'java', '.go': 'go', '.rb': 'ruby', '.php': 'php', '.cs': 'c_sharp',
            # Ngôn ngữ C/C++ cần cẩn thận với header files
            '.c': 'c', '.h': 'c', # Coi .h là C
            '.cpp': 'cpp', '.hpp': 'cpp', '.cxx': 'cpp', '.hxx': 'cpp', # Coi header C++ là cpp
        }
        ignored_parts = {'.git', 'node_modules', '__pycache__', 'venv', 'target', 'build', 'dist', '.idea', '.vscode', '.settings', 'bin', 'obj', 'lib', 'docs', 'examples', 'tests', 'test', 'samples'}
        ignored_extensions = {'.log', '.tmp', '.swp', '.DS_Store', '.map', '.min.js', '.min.css', '.lock', '.cfg', '.ini', '.txt', '.md', '.json', '.xml', '.yaml', '.yml', '.csv', '.tsv', '.bak', '.old', '.orig', '.zip', '.tar.gz', '.rar', '.7z', '.exe', '.dll', '.so', '.o', '.a', '.lib', '.jar', '.class', '.pyc', '.pyd', '.egg-info', '.pytest_cache', '.mypy_cache', '.tox', '.nox', '.hypothesis', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico', '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.mp3', '.mp4', '.avi', '.mov'}


        for file_p in source_path_obj.rglob('*'):
            relative_path_parts = file_p.relative_to(source_path_obj).parts
            if any(part in ignored_parts for part in relative_path_parts) or \
                (file_p.name.startswith('.') and file_p.is_dir() and file_p.name != "."): # Bỏ qua thư mục ẩn trừ thư mục gốc '.'
                if file_p.is_dir(): logger.debug(f"CKGBuilder: Skipping ignored directory: {file_p.relative_to(source_path_obj)}")
                continue

            if file_p.is_file():
                if file_p.name.startswith('.') or file_p.suffix.lower() in ignored_extensions:
                    logger.debug(f"CKGBuilder: Skipping ignored file: {file_p.relative_to(source_path_obj)}")
                    continue
                
                file_lang = common_code_extensions.get(file_p.suffix.lower())
                
                # Nếu project có ngôn ngữ chính được định nghĩa, có thể chỉ ưu tiên parse ngôn ngữ đó
                # hoặc dùng nó làm fallback. Hiện tại, chỉ parse nếu có trong common_code_extensions.
                # if project_main_language and file_lang != project_main_language:
                #     logger.debug(f"CKGBuilder: Skipping file {file_p.relative_to(source_path_obj)} as its language '{file_lang}' does not match project's main language '{project_main_language}'.")
                #     continue

                if file_lang:
                    files_to_process.append((file_p, file_lang))
                else:
                    logger.debug(f"CKGBuilder: Skipping file with unsupported/unknown code extension for CKG: {file_p.relative_to(source_path_obj)}")
        
        logger.info(f"CKGBuilder: Found {len(files_to_process)} files to process for CKG.")

        for file_p, lang_to_use in files_to_process:
            file_path_in_repo = str(file_p.relative_to(source_path_obj))
            try:
                try:
                    content = file_p.read_text(encoding='utf-8')
                except UnicodeDecodeError:
                    logger.warning(f"CKGBuilder: UTF-8 decode error for {file_path_in_repo}. Trying 'latin-1'.")
                    try:
                        content = file_p.read_text(encoding='latin-1')
                    except Exception as e_enc:
                        logger.error(f"CKGBuilder: Could not read file {file_path_in_repo} due to encoding: {e_enc}. Skipping.")
                        continue
                
                await self.process_file_for_ckg(file_path_in_repo, content, lang_to_use)
                files_processed_count += 1
            except Exception as e:
                logger.error(f"CKGBuilder: Critical error processing file {file_path_in_repo} for CKG: {e}", exc_info=True)
        
        logger.info(f"CKGBuilder: Finished CKG build for project '{self.project_graph_id}'. Processed {files_processed_count} files for CKG.")
        return files_processed_count