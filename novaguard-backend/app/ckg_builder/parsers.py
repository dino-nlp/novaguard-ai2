# novaguard-ai2/novaguard-backend/app/ckg_builder/parsers.py
import logging
from typing import List, Dict, Any, Tuple, Optional, Set
from tree_sitter import Language, Parser, Node, Query
from tree_sitter_languages import get_language # Sử dụng get_language thay vì get_parser trực tiếp

logger = logging.getLogger(__name__)

# --- Data Structures ---
class ExtractedFunction:
    def __init__(self, name: str, start_line: int, end_line: int, signature: Optional[str] = None, class_name: Optional[str] = None, body_node: Optional[Node] = None, parameters_str: Optional[str] = None):
        self.name = name
        self.start_line = start_line
        self.end_line = end_line
        self.signature = signature
        self.parameters_str = parameters_str
        self.class_name = class_name
        self.body_node = body_node
        # Tuple: (called_name, base_object_name_or_None, call_type, call_site_line_number)
        self.calls: Set[Tuple[str, Optional[str], Optional[str], int]] = set()

class ExtractedClass:
    def __init__(self, name: str, start_line: int, end_line: int, body_node: Optional[Node] = None):
        self.name = name
        self.start_line = start_line
        self.end_line = end_line
        self.body_node = body_node
        self.methods: List[ExtractedFunction] = []
        self.superclasses: Set[str] = set() # Set các tên class cha

class ExtractedImport:
    def __init__(self, import_type: str, module_path: Optional[str] = None, imported_names: Optional[List[Tuple[str, Optional[str]]]] = None):
        # import_type: "direct" (import foo), "from" (from foo import bar)
        # module_path: "foo.bar.baz"
        # imported_names: list of (original_name, alias_name_or_None)
        self.import_type = import_type
        self.module_path = module_path
        self.imported_names = imported_names if imported_names else []


class ParsedFileResult:
    def __init__(self, file_path: str, language: str):
        self.file_path = file_path
        self.language = language
        self.functions: List[ExtractedFunction] = []
        self.classes: List[ExtractedClass] = []
        self.imports: List[ExtractedImport] = []

class BaseCodeParser:
    def __init__(self, language_name: str):
        self.language_name = language_name
        try:
            self.lang_object: Language = get_language(language_name)
            self.parser: Parser = Parser()
            self.parser.set_language(self.lang_object)
            logger.info(f"Tree-sitter parser for '{language_name}' initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to load tree-sitter language grammar for '{language_name}': {e}. ")
            raise ValueError(f"Could not initialize parser for {language_name}") from e

    def parse(self, code_content: str, file_path: str) -> Optional[ParsedFileResult]:
        if not self.parser or not self.lang_object:
            logger.error(f"Parser for {self.language_name} not properly initialized for file {file_path}.")
            return None
        try:
            tree = self.parser.parse(bytes(code_content, "utf8"))
            result = ParsedFileResult(file_path=file_path, language=self.language_name)
            if tree.root_node.has_error: # Kiểm tra lỗi cú pháp cơ bản
                logger.warning(f"Syntax errors found in file {file_path} during parsing. CKG data might be incomplete.")
            self._extract_entities(tree.root_node, result)
            return result
        except Exception as e:
            logger.error(f"Error parsing file {file_path} with {self.language_name} parser: {e}", exc_info=True)
            return None

    def _extract_entities(self, root_node: Node, result: ParsedFileResult):
        logger.debug(
            f"PythonParser Extracted from {result.file_path}: "
            f"{len(result.imports)} imports, "
            f"{len(result.classes)} classes ("
            f"{sum(len(c.methods) for c in result.classes)} methods), "
            f"{len(result.functions)} global functions."
        )
        raise NotImplementedError("Subclasses must implement _extract_entities")

    def _get_node_text(self, node: Optional[Node]) -> Optional[str]:
        return node.text.decode('utf8') if node else None

    def _get_line_number(self, node: Node) -> int:
        return node.start_point[0] + 1

    def _get_end_line_number(self, node: Node) -> int:
        return node.end_point[0] + 1


class PythonParser(BaseCodeParser):
    def __init__(self):
        super().__init__("python")
        # Cải thiện Queries
        self.queries = {
            "imports": self.lang_object.query("""
                (import_statement name: (dotted_name) @module_path) @import_direct
                (import_statement name: (aliased_import path: (dotted_name) @module_path alias: (identifier) @alias)) @import_direct_alias

                (import_from_statement
                    module_name: (dotted_name)? @from_module_path
                    (
                        (wildcard_import) @wildcard_import |
                        (dotted_name (identifier) @name (comment)? ) @import_name |
                        (aliased_import name: (identifier) @name alias: (identifier) @alias) @import_name_alias
                    )
                ) @import_from
            """),
            "classes": self.lang_object.query("""
                (class_definition
                    name: (identifier) @class.name
                    superclasses: (argument_list . (_) @superclass)? ;; Capture the whole argument or identifier
                    body: (block) @class.body
                ) @class.definition
            """),
            "functions_and_methods": self.lang_object.query("""
                (function_definition
                    name: (identifier) @function.name
                    parameters: (parameters) @function.parameters
                    return_type: (type)? @function.return_type
                    body: (block) @function.body
                ) @function.definition
            """),
            "calls": self.lang_object.query("""
                (call
                    function: [
                        (identifier) @func_name
                        (attribute object: (identifier) @obj_name attribute: (identifier) @method_name)
                        (attribute object: (call) @chained_call_obj attribute: (identifier) @method_name)
                        (attribute object: (subscript) @subscript_obj attribute: (identifier) @method_name)
                        (attribute object: (attribute) @nested_attr_obj attribute: (identifier) @method_name)
                    ]
                    arguments: (_)? @arguments
                ) @call_expression
            """)
        }

    def _extract_imports(self, root_node: Node, result: ParsedFileResult):
        for match_idx, match_obj in enumerate(self.queries["imports"].matches(root_node)):
            # `match_obj` là một `QueryMatch` object
            # `match_obj.captures` là list các `(QueryCapture, Node)`
            # `match_obj.pattern_index` cho biết query nào trong file .scm đã match (nếu có nhiều)

            # Xác định capture chính (ví dụ: @import_direct, @import_from)
            # Giả sử query được cấu trúc tốt, pattern_index có thể giúp
            main_capture_node = match_obj.nodes[0] # Node bao trùm match

            if main_capture_node.type == "import_statement":
                module_path_node = None
                alias_node = None
                for cap_idx, cap_name in self.queries["imports"].capture_names: # Lấy capture names từ query
                    for node_captured, name_str in match_obj.captures:
                        if name_str == "module_path" and node_captured.parent == main_capture_node: # Đảm bảo capture thuộc match hiện tại
                             module_path_node = node_captured
                        elif name_str == "alias" and node_captured.parent == main_capture_node.child_by_field_name('name'): # Kiểm tra parent của alias
                             alias_node = node_captured
                
                if module_path_node:
                    result.imports.append(ExtractedImport(
                        import_type="direct",
                        module_path=self._get_node_text(module_path_node),
                        imported_names=[(self._get_node_text(module_path_node), self._get_node_text(alias_node))] if alias_node else None
                    ))

            elif main_capture_node.type == "import_from_statement":
                from_module_path_node = None
                imported_items: List[Tuple[str, Optional[str]]] = []
                
                is_wildcard = False
                for cap_idx, cap_name in self.queries["imports"].capture_names:
                    for node_captured, name_str in match_obj.captures:
                        if node_captured.parent.parent != main_capture_node and node_captured.parent != main_capture_node : # Chỉ xử lý capture của match này
                            if node_captured.type != 'dotted_name' and node_captured.type != 'identifier' and node_captured.type != 'aliased_import' and node_captured.type != 'wildcard_import':
                                continue

                        if name_str == "from_module_path": from_module_path_node = node_captured
                        elif name_str == "wildcard_import": is_wildcard = True
                        elif name_str == "import_name" or name_str == "name": # 'name' for both direct name and alias original name
                            # Xử lý trường hợp (aliased_import name: (identifier) @name alias: (identifier) @alias)
                            imported_name_text = self._get_node_text(node_captured)
                            found_alias_node = None
                            # Kiểm tra xem node_captured có phải là phần 'name' của 'aliased_import' không
                            if node_captured.parent and node_captured.parent.type == 'aliased_import':
                                for child_alias_node in node_captured.parent.children:
                                    if child_alias_node.type == 'identifier' and child_alias_node != node_captured: # child còn lại là alias
                                        # Cần kiểm tra field name của child_alias_node là 'alias'
                                        if node_captured.parent.child_by_field_name('alias') == child_alias_node:
                                            found_alias_node = child_alias_node
                                            break
                            imported_items.append((imported_name_text, self._get_node_text(found_alias_node)))


                if from_module_path_node:
                    if is_wildcard:
                        result.imports.append(ExtractedImport(
                            import_type="from_wildcard",
                            module_path=self._get_node_text(from_module_path_node),
                            imported_names=[("*", None)]
                        ))
                    elif imported_items:
                         result.imports.append(ExtractedImport(
                            import_type="from",
                            module_path=self._get_node_text(from_module_path_node),
                            imported_names=imported_items
                        ))

    def _extract_calls(self, scope_node: Node, current_owner_entity: ExtractedFunction, result: ParsedFileResult):
        if not scope_node: return
        for match_obj in self.queries["calls"].matches(scope_node):
            # call_expression_node là node (call ...)
            call_expression_node = None
            for node_candidate, name_str in match_obj.captures:
                if name_str == "call_expression":
                    call_expression_node = node_candidate
                    break
            if not call_expression_node:
                continue

            call_name_node = None
            obj_name_node = None # Simple object identifier: obj.method
            method_name_node = None
            # Các node phức tạp hơn cho base object
            # chained_call_obj_node = None
            # subscript_obj_node = None
            # nested_attr_obj_node = None

            call_type = "unknown"
            called_name_str: Optional[str] = None
            base_object_name_str: Optional[str] = None

            # Phân tích các capture trong một match cụ thể
            for node_captured, capture_name_str in match_obj.captures:
                # Đảm bảo capture này thuộc về call_expression_node hiện tại
                # (Cần kiểm tra parent hoặc containment nếu query phức tạp hơn)
                if capture_name_str == "func_name_direct":
                    call_name_node = node_captured
                elif capture_name_str == "obj_name":
                    obj_name_node = node_captured
                elif capture_name_str == "method_name":
                    method_name_node = node_captured
                # elif capture_name_str == "chained_call_obj":
                #     chained_call_obj_node = node_captured
                # elif capture_name_str == "subscript_obj":
                #     subscript_obj_node = node_captured
                # elif capture_name_str == "nested_attr_obj":
                #     nested_attr_obj_node = node_captured

            if method_name_node: # Ưu tiên method call nếu có method_name
                call_type = "method"
                called_name_str = self._get_node_text(method_name_node)
                if obj_name_node: # obj.method()
                    base_object_name_str = self._get_node_text(obj_name_node)
                # else: # Base object phức tạp hơn, ví dụ get_obj().method()
                      # base_object_name_str có thể để là None hoặc cố gắng parse (khá phức tạp)
                      # Hiện tại, chúng ta sẽ để là None nếu không phải (identifier) đơn giản
                    # logger.debug(f"CKG Call: Complex base object for method call '{called_name_str}' in '{current_owner_entity.name}'.")
                    pass

            elif call_name_node: # func()
                call_type = "direct" # Hoặc "function"
                called_name_str = self._get_node_text(call_name_node)
                base_object_name_str = None # Không có base object cho direct call

            if called_name_str:
                # Lấy dòng của lệnh gọi (ví dụ: dòng bắt đầu của node `call_expression_node`)
                call_site_line = self._get_line_number(call_expression_node)

                current_owner_entity.calls.add(
                    (called_name_str, base_object_name_str, call_type, call_site_line)
                )
                logger.debug(
                    f"CKG Call: In '{current_owner_entity.name}' (L{current_owner_entity.start_line} in {result.file_path}), "
                    f"found call at L{call_site_line}: "
                    f"{base_object_name_str + '.' if base_object_name_str else ''}{called_name_str}() "
                    f"type: {call_type}"
                )
            else:
                logger.warning(f"CKG Call: Could not determine called_name for a call expression in '{current_owner_entity.name}' (L{current_owner_entity.start_line} in {result.file_path}) at node: {call_expression_node.text.decode('utf8')[:50]}")

    def _extract_functions_and_methods(self, scope_node: Node, result: ParsedFileResult, current_class_obj: Optional[ExtractedClass] = None):
        # scope_node có thể là root_node (cho global functions) hoặc class_body_node (cho methods)
        if not scope_node: return

        # matches trả về list các QueryMatch object
        for match_obj in self.queries["functions_and_methods"].matches(scope_node):
            # Check if this match is a direct child of the scope_node or deeper.
            # For global functions, we only want those directly under module (or certain blocks like if __name__ == '__main__').
            # For methods, they should be directly under the class block.
            func_def_node = None
            for node_candidate, _ in match_obj.captures: # Tìm node (function_definition)
                if node_candidate.type == 'function_definition':
                    func_def_node = node_candidate
                    break
            if not func_def_node: continue

            # Logic để tránh xử lý function definition bên trong một function definition khác (nested func)
            # hoặc đảm bảo nó thuộc đúng scope (global hoặc class)
            is_valid_scope = False
            if current_class_obj: # Đang tìm methods
                if func_def_node.parent == scope_node: # scope_node là class_body_node
                    is_valid_scope = True
            else: # Đang tìm global functions
                # Global functions có thể nằm trực tiếp dưới module, hoặc trong if, try, for, while ở global scope
                # Đây là một heuristic đơn giản, có thể cần cải thiện
                if func_def_node.parent == scope_node or \
                   (func_def_node.parent and func_def_node.parent.type == 'block' and func_def_node.parent.parent == scope_node):
                    is_valid_scope = True

            if not is_valid_scope: continue


            func_name = ""
            params_str = ""
            return_type_str = None
            func_body_node = None

            # `captures` trả về list các (Node, capture_name_str)
            for node_captured, capture_name_str in match_obj.captures:
                if capture_name_str == "function.name": func_name = self._get_node_text(node_captured)
                elif capture_name_str == "function.parameters": params_str = self._get_node_text(node_captured)
                elif capture_name_str == "function.return_type": return_type_str = self._get_node_text(node_captured)
                elif capture_name_str == "function.body": func_body_node = node_captured
            
            if func_name:
                signature = f"{params_str}"
                if return_type_str:
                    signature += f" -> {return_type_str}"

                func_obj = ExtractedFunction(
                    name=func_name,
                    start_line=self._get_line_number(func_def_node),
                    end_line=self._get_end_line_number(func_def_node),
                    signature=signature.strip(),
                    parameters_str=params_str.strip(),
                    class_name=current_class_obj.name if current_class_obj else None,
                    body_node=func_body_node
                )
                self._extract_calls(func_body_node, func_obj, result)

                if current_class_obj:
                    current_class_obj.methods.append(func_obj)
                else:
                    result.functions.append(func_obj)


    def _extract_classes(self, root_node: Node, result: ParsedFileResult):
        for match_obj in self.queries["classes"].matches(root_node):
            class_def_node = None
            for node_candidate, _ in match_obj.captures:
                if node_candidate.type == 'class_definition':
                    class_def_node = node_candidate
                    break
            if not class_def_node: continue

            class_name = ""
            class_body_node = None
            superclasses_set: Set[str] = set()

            for node_captured, capture_name_str in match_obj.captures:
                if capture_name_str == "class.name": class_name = self._get_node_text(node_captured)
                elif capture_name_str == "class.body": class_body_node = node_captured
                elif capture_name_str == "superclass":
                    # Node @superclass có thể là identifier, attribute (a.B), call (Meta()), etc.
                    # Cần xử lý để lấy tên class cha. Đơn giản nhất là lấy text.
                    sc_text = self._get_node_text(node_captured)
                    if sc_text: superclasses_set.add(sc_text)

            if class_name and class_body_node:
                class_obj = ExtractedClass(
                    name=class_name,
                    start_line=self._get_line_number(class_def_node),
                    end_line=self._get_end_line_number(class_def_node),
                    body_node=class_body_node
                )
                class_obj.superclasses = superclasses_set
                # Gọi đệ quy để tìm methods bên trong class body
                self._extract_functions_and_methods(class_body_node, result, class_obj)
                result.classes.append(class_obj)


    def _extract_entities(self, root_node: Node, result: ParsedFileResult):
        self._extract_imports(root_node, result)
        self._extract_classes(root_node, result) # Phải trước global functions để methods được gán đúng
        self._extract_functions_and_methods(root_node, result, current_class_obj=None) # Global functions
        
        logger.debug(
            f"PythonParser Extracted from {result.file_path}: "
            f"{len(result.imports)} imports, "
            f"{len(result.classes)} classes ("
            f"{sum(len(c.methods) for c in result.classes)} methods), "
            f"{len(result.functions)} global functions."
        )


_parsers_cache = {}
def get_code_parser(language: str) -> Optional[BaseCodeParser]:
    language_key = language.lower().strip()
    if not language_key:
        return None
    if language_key in _parsers_cache:
        return _parsers_cache[language_key]

    parser_instance: Optional[BaseCodeParser] = None
    if language_key == "python":
        parser_instance = PythonParser()
    # elif language_key == "javascript":
    #     parser_instance = JavaScriptParser() # TODO: Implement JavaScriptParser
    else:
        logger.warning(f"No specific parser class implemented for language: '{language}'. Tree-sitter grammar might be available but entities won't be extracted.")
        # You could try a generic BaseCodeParser if you only want the tree, but it won't extract entities.
        # try:
        #     parser_instance = BaseCodeParser(language_key) # This would fail due to NotImplementedError
        # except ValueError: # Raised if grammar cannot be loaded
        #     return None

    if parser_instance:
        _parsers_cache[language_key] = parser_instance
    return parser_instance