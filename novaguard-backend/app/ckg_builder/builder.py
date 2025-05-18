# novaguard-backend/app/ckg_builder/builder.py
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
from neo4j import AsyncDriver

from app.core.graph_db import get_async_neo4j_driver
from app.models import Project
from .parsers import get_code_parser, ParsedFileResult, ExtractedFunction, ExtractedClass, ExtractedImport

logger = logging.getLogger(__name__)

class CKGBuilder:
    def __init__(self, project_model: Project, neo4j_driver: Optional[AsyncDriver] = None):
        self.project = project_model
        self.project_graph_id = f"novaguard_project_{project_model.id}"
        self.file_path_to_node_id_cache: Dict[str, int] = {} # Cache này có thể không cần nếu dùng composite_id
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
        db_name = getattr(driver, 'database', 'neo4j')
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
        ON MATCH SET p.name = $repo_name, p.language = $language, p.novaguard_id = $novaguard_project_id
        """ # Thêm novaguard_id vào ON MATCH
        params = {
            "project_graph_id": self.project_graph_id,
            "repo_name": self.project.repo_name,
            "novaguard_project_id": self.project.id,
            "language": self.project.language
        }
        await self._execute_write_queries([(query, params)])
        logger.info(f"CKGBuilder: Ensured Project node '{self.project_graph_id}' exists/updated.")

    async def _clear_existing_data_for_file(self, file_path_in_repo: str):
        file_composite_id = f"{self.project_graph_id}:{file_path_in_repo}"
        queries = [
            (
                """
                MATCH (file_node:File {composite_id: $file_composite_id})
                OPTIONAL MATCH (file_node)<-[:DEFINED_IN]-(entity)
                OPTIONAL MATCH (entity)-[r_calls_from:CALLS]->()
                DELETE r_calls_from
                WITH file_node, entity
                DETACH DELETE entity
                WITH file_node
                DETACH DELETE file_node
                """,
                {"file_composite_id": file_composite_id}
            )
        ]
        logger.info(f"CKGBuilder: Clearing existing CKG data for file '{file_path_in_repo}' in project '{self.project_graph_id}'")
        await self._execute_write_queries(queries)


    async def process_file_for_ckg(self, file_path_in_repo: str, file_content: str, language: Optional[str]):
        if not language:
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

        await self._clear_existing_data_for_file(file_path_in_repo)

        cypher_batch: List[Tuple[str, Dict[str, Any]]] = []
        file_composite_id = f"{self.project_graph_id}:{file_path_in_repo}"

        # 1. File Node
        file_node_props = {
            "path": file_path_in_repo,
            "project_graph_id": self.project_graph_id,
            "language": language,
            "name": Path(file_path_in_repo).name,
            "composite_id": file_composite_id
        }
        cypher_batch.append((
            "MERGE (p:Project {graph_id: $project_graph_id}) "
            "MERGE (f:File {composite_id: $composite_id}) "
            "ON CREATE SET f = $props "
            "ON MATCH SET f.language = $props.language, f.name = $props.name "
            "MERGE (f)-[:PART_OF_PROJECT]->(p)",
            {
                "project_graph_id": self.project_graph_id,
                "composite_id": file_composite_id,
                "props": file_node_props
            }
        ))

        # 2. Import Nodes and Relationships
        for imp_data in parsed_data.imports:
            if imp_data.module_path:
                cypher_batch.append((
                    """
                    MERGE (m:Module {path: $module_path, project_graph_id: $project_graph_id})
                    ON CREATE SET m.name = last(split($module_path, '.'))
                    WITH m
                    MATCH (f:File {composite_id: $file_composite_id})
                    MERGE (f)-[r:IMPORTS_MODULE]->(m)
                    SET r.type = $import_type, r.original_names = $original_names, r.aliases = $aliases
                    """,
                    {
                        "module_path": imp_data.module_path,
                        "project_graph_id": self.project_graph_id,
                        "file_composite_id": file_composite_id,
                        "import_type": imp_data.import_type,
                        "original_names": [name for name, alias in imp_data.imported_names] if imp_data.imported_names else [],
                        "aliases": [alias for name, alias in imp_data.imported_names if alias] if imp_data.imported_names else []
                    }
                ))
        # 3. Class Nodes, Methods, and Inheritance
        for cls_data in parsed_data.classes:
            class_composite_id = f"{self.project_graph_id}:{file_path_in_repo}:{cls_data.name}:{cls_data.start_line}"
            class_props = {
                "name": cls_data.name, "file_path": file_path_in_repo,
                "start_line": cls_data.start_line, "end_line": cls_data.end_line,
                "project_graph_id": self.project_graph_id,
                "composite_id": class_composite_id
            }
            cypher_batch.append((
                """
                MATCH (f:File {composite_id: $file_composite_id})
                MERGE (c:Class {composite_id: $composite_id})
                ON CREATE SET c = $props_on_create
                ON MATCH SET c.end_line = $props_on_create.end_line
                MERGE (c)-[:DEFINED_IN]->(f)
                """,
                {
                    "file_composite_id": file_composite_id,
                    "composite_id": class_composite_id,
                    "props_on_create": class_props
                }
            ))

            for super_name in cls_data.superclasses:
                cypher_batch.append((
                    """
                    MATCH (this_class:Class {composite_id: $class_composite_id})
                    MERGE (super_class:Class {name: $super_name, project_graph_id: $project_graph_id})
                    ON CREATE SET super_class.placeholder = true, 
                                  super_class.file_path = "UNKNOWN",
                                  super_class.start_line = -1,       
                                  super_class.composite_id = $project_graph_id + ":UNKNOWN:" + $super_name + ":-1"
                    MERGE (this_class)-[:INHERITS_FROM]->(super_class)
                    """,
                    {
                        "class_composite_id": class_composite_id,
                        "super_name": super_name,
                        "project_graph_id": self.project_graph_id
                    }
                ))

            for method_data in cls_data.methods:
                method_composite_id = f"{self.project_graph_id}:{file_path_in_repo}:{method_data.name}:{method_data.start_line}"
                method_props = {
                    "name": method_data.name, "file_path": file_path_in_repo,
                    "start_line": method_data.start_line, "end_line": method_data.end_line,
                    "signature": method_data.signature, "parameters": method_data.parameters_str,
                    "class_name": cls_data.name, "project_graph_id": self.project_graph_id,
                    "composite_id": method_composite_id
                }
                cypher_batch.append((
                    """
                    MATCH (f:File {composite_id: $file_composite_id})
                    MATCH (cls:Class {composite_id: $class_composite_id})
                    MERGE (m:Method:Function {composite_id: $composite_id})
                    ON CREATE SET m = $props
                    ON MATCH SET m.end_line = $props.end_line, m.signature = $props.signature, 
                                 m.parameters = $props.parameters, m.class_name = $props.class_name
                    MERGE (m)-[:DEFINED_IN]->(f)
                    MERGE (m)-[:DEFINED_IN_CLASS]->(cls)
                    """,
                    {
                        "file_composite_id": file_composite_id,
                        "class_composite_id": class_composite_id,
                        "composite_id": method_composite_id,
                        "props": method_props
                    }
                ))

        # 4. Global Function Nodes
        for func_data in parsed_data.functions:
            func_composite_id = f"{self.project_graph_id}:{file_path_in_repo}:{func_data.name}:{func_data.start_line}"
            func_props = {
                "name": func_data.name, "file_path": file_path_in_repo,
                "start_line": func_data.start_line, "end_line": func_data.end_line,
                "signature": func_data.signature, "parameters": func_data.parameters_str,
                "project_graph_id": self.project_graph_id,
                "composite_id": func_composite_id
            }
            cypher_batch.append((
                """
                MATCH (f:File {composite_id: $file_composite_id})
                MERGE (fn:Function {composite_id: $composite_id})
                ON CREATE SET fn = $props
                ON MATCH SET fn.end_line = $props.end_line, fn.signature = $props.signature, fn.parameters = $props.parameters
                MERGE (fn)-[:DEFINED_IN]->(f)
                """,
                {
                    "file_composite_id": file_composite_id,
                    "composite_id": func_composite_id,
                    "props": func_props
                }
            ))

        if cypher_batch:
            logger.debug(f"CKGBuilder: Executing initial batch of {len(cypher_batch)} Cypher queries for file {file_path_in_repo}.")
            await self._execute_write_queries(cypher_batch)

        # 5. Link CALLS
        call_link_queries_batch: List[Tuple[str, Dict[str, Any]]] = []
        all_defined_entities_in_file = parsed_data.functions + [
            method for cls_data in parsed_data.classes for method in cls_data.methods
        ]

        for defined_entity in all_defined_entities_in_file:
            if not defined_entity.calls:
                continue

            caller_label = ":Method:Function" if defined_entity.class_name else ":Function"
            caller_composite_id = f"{self.project_graph_id}:{file_path_in_repo}:{defined_entity.name}:{defined_entity.start_line}"

            for called_name, base_object_name, call_type in defined_entity.calls:
                call_params = {
                    "caller_composite_id": caller_composite_id,
                    "callee_name": called_name,
                    "project_graph_id": self.project_graph_id,
                    "caller_file_path_for_local_search": file_path_in_repo,
                    "call_type_prop": call_type,
                    "base_object_prop": base_object_name
                    # "current_class_name_if_any": defined_entity.class_name # Để tìm method trong cùng class
                }

                # Cypher query để tìm callee và tạo CALLS relationship.
                # Ưu tiên tìm trong cùng file, sau đó trong cùng class (nếu caller là method), rồi mới đến toàn project.
                # Node callee được tìm thấy sẽ có composite_id của nó.
                resolve_query = f"""
                MATCH (caller{caller_label} {{composite_id: $caller_composite_id}})

                // Attempt 1: Callee is a function/method in the same file as the caller
                OPTIONAL MATCH (callee_same_file:Function {{
                    name: $callee_name,
                    file_path: $caller_file_path_for_local_search,
                    project_graph_id: $project_graph_id
                }})
                WITH caller, callee_same_file

                // Attempt 2: If caller is a method, try to find callee as another method in the same class or a superclass method.
                // This is a simplified version. True resolution would require walking the MRO.
                OPTIONAL MATCH (caller)-[:DEFINED_IN_CLASS]->(caller_class:Class)
                WHERE $call_type_prop = "method" AND callee_same_file IS NULL // Only if method call and not found yet
                OPTIONAL MATCH (callee_in_same_or_superclass:Method {{
                    name: $callee_name,
                    project_graph_id: $project_graph_id
                }})
                WHERE (callee_in_same_or_superclass)-[:DEFINED_IN_CLASS]->(caller_class) OR 
                      ( (caller_class)-[:INHERITS_FROM*0..5]->(:Class)<-[:DEFINED_IN_CLASS]-(callee_in_same_or_superclass) AND
                        callee_in_same_or_superclass.name = $callee_name ) // Check name explicitly for superclass methods
                WITH caller, callee_same_file, COALESCE(callee_same_file, callee_in_same_or_superclass) as callee_local_or_class_level

                // Attempt 3: Callee is any function/method in the project if not found by previous, more specific attempts
                OPTIONAL MATCH (callee_in_project:Function {{
                    name: $callee_name,
                    project_graph_id: $project_graph_id
                }})
                WHERE callee_local_or_class_level IS NULL // Only if not found by more specific matches

                // Final Callee: Prioritize matches: same_file > same_class/superclass > any_in_project
                WITH caller, COALESCE(callee_local_or_class_level, callee_in_project) AS callee
                
                WHERE callee IS NOT NULL // Proceed only if a callee is found
                MERGE (caller)-[r:CALLS {{
                    type: $call_type_prop,
                    base_object: $base_object_prop
                    // Thêm line number của call site nếu parser cung cấp
                    // call_site_line: $call_site_line_param
                }}]->(callee)
                """
                call_link_queries_batch.append((resolve_query, call_params))

        if call_link_queries_batch:
            logger.debug(f"CKGBuilder: Executing batch of {len(call_link_queries_batch)} CALLS link queries for file {file_path_in_repo}.")
            await self._execute_write_queries(call_link_queries_batch)

        logger.info(f"CKGBuilder: Finished CKG processing for file '{file_path_in_repo}'.")

    async def build_for_project_from_path(self, repo_local_path: str):
        logger.info(f"CKGBuilder: Starting CKG build for project '{self.project_graph_id}' from path: {repo_local_path}")
        await self._ensure_project_node()

        source_path_obj = Path(repo_local_path)
        files_processed_count = 0
        files_to_process: List[Tuple[Path, Optional[str]]] = []

        # Hint ngôn ngữ chính của dự án từ model Project
        project_main_language = self.project.language.lower().strip() if self.project.language else None
        logger.info(f"CKGBuilder: Project main language hint: {project_main_language}")

        # Danh sách các extension code phổ biến và ngôn ngữ tương ứng
        common_code_extensions = {
            '.py': 'python', '.js': 'javascript', '.jsx': 'javascript',
            '.ts': 'typescript', '.tsx': 'typescript',
            '.java': 'java', '.go': 'go', '.rb': 'ruby', '.php': 'php', '.cs': 'c_sharp',
            '.c': 'c', '.h': 'c',
            '.cpp': 'cpp', '.hpp': 'cpp', '.cxx': 'cpp', '.hxx': 'cpp',
        }
        # Các thư mục/file cần bỏ qua
        # (Nên lấy từ một file config hoặc cấu hình project sau này)
        ignored_parts = {
            '.git', 'node_modules', '__pycache__', 'venv', 'target', 'build', 'dist',
            '.idea', '.vscode', '.settings', 'bin', 'obj', 'lib', 'docs', 'examples',
            'tests', 'test', 'samples', # Cân nhắc việc có parse code test không
            '.DS_Store', 'coverage', '.pytest_cache', '.mypy_cache', '.tox', '.nox',
            'site-packages', 'dist-packages', 'migrations', 'static', 'media', 'templates',
            'vendor', 'third_party'
        }
        ignored_extensions = {
            '.log', '.tmp', '.swp', '.map', '.min.js', '.min.css', '.lock', '.cfg', '.ini',
            '.txt', '.md', '.json', '.xml', '.yaml', '.yml', '.csv', '.tsv', '.bak', '.old', '.orig',
            '.zip', '.tar.gz', '.rar', '.7z', '.exe', '.dll', '.so', '.o', '.a', '.lib',
            '.jar', '.class', '.pyc', '.pyd', '.egg-info', '.hypothesis',
            '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico', '.pdf', '.doc', '.docx',
            '.xls', '.xlsx', '.ppt', '.pptx', '.mp3', '.mp4', '.avi', '.mov',
            '.db', '.sqlite', '.sqlite3'
        }
        min_file_size_for_ckg = 10 # bytes, bỏ qua các file quá nhỏ (ví dụ: __init__.py rỗng)
        max_file_size_for_ckg = 5 * 1024 * 1024 # 5MB, bỏ qua file quá lớn

        for file_p in source_path_obj.rglob('*'):
            if not file_p.is_file():
                continue

            relative_path_parts = file_p.relative_to(source_path_obj).parts
            if any(part.lower() in ignored_parts for part in relative_path_parts) or \
               any(file_p.name.lower().endswith(ext) for ext in ignored_parts if not ext.startswith('.')) or \
               (file_p.name.startswith('.') and file_p.name not in ['.env', '.flaskenv']): # Bỏ qua file ẩn trừ một số file cụ thể
                logger.debug(f"CKGBuilder: Skipping ignored file/path: {file_p.relative_to(source_path_obj)}")
                continue

            if file_p.suffix.lower() in ignored_extensions:
                logger.debug(f"CKGBuilder: Skipping file with ignored extension: {file_p.relative_to(source_path_obj)}")
                continue

            try:
                file_size = file_p.stat().st_size
                if file_size < min_file_size_for_ckg:
                    logger.debug(f"CKGBuilder: Skipping too small file: {file_p.relative_to(source_path_obj)} ({file_size} bytes)")
                    continue
                if file_size > max_file_size_for_ckg:
                    logger.warning(f"CKGBuilder: Skipping too large file: {file_p.relative_to(source_path_obj)} ({file_size} bytes)")
                    continue
            except OSError: # Có thể xảy ra với broken symlinks
                logger.warning(f"CKGBuilder: Could not stat file (possibly broken symlink): {file_p.relative_to(source_path_obj)}. Skipping.")
                continue

            file_lang = common_code_extensions.get(file_p.suffix.lower())
            if project_main_language and file_lang != project_main_language:
                 # Nếu dự án có ngôn ngữ chính, có thể chỉ ưu tiên phân tích ngôn ngữ đó trong lần đầu
                 # Hoặc tùy chọn cho phép phân tích nhiều ngôn ngữ.
                 # Hiện tại, chúng ta vẫn thêm vào để phân tích nếu có parser.
                 pass

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
                except OSError as e_os_read: # Ví dụ file bị xóa giữa chừng
                    logger.error(f"CKGBuilder: OS error reading file {file_path_in_repo}: {e_os_read}. Skipping.")
                    continue


                await self.process_file_for_ckg(file_path_in_repo, content, lang_to_use)
                files_processed_count += 1
            except Exception as e:
                logger.error(f"CKGBuilder: Critical error processing file {file_path_in_repo} for CKG: {e}", exc_info=True)
                # Cân nhắc việc có nên dừng toàn bộ quá trình build CKG của project nếu một file lỗi nặng không.
                # Hoặc chỉ bỏ qua file đó.

        logger.info(f"CKGBuilder: Finished CKG build for project '{self.project_graph_id}'. Processed {files_processed_count} files for CKG.")
        return files_processed_count