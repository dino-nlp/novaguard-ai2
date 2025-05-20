# novaguard-ai2/novaguard-backend/app/ckg_builder/parsers.py
import logging
from typing import List, Dict, Any, Tuple, Optional, Set
from tree_sitter import Language, Parser, Node, Query # type: ignore
from tree_sitter_languages import get_language

logger = logging.getLogger(__name__)

# --- Data Structures (giữ nguyên) ---
class ExtractedFunction:
    def __init__(self, name: str, start_line: int, end_line: int, signature: Optional[str] = None, class_name: Optional[str] = None, body_node: Optional[Node] = None, parameters_str: Optional[str] = None):
        self.name = name
        self.start_line = start_line
        self.end_line = end_line
        self.signature = signature
        self.parameters_str = parameters_str
        self.class_name = class_name
        self.body_node = body_node
        self.calls: Set[Tuple[str, Optional[str], Optional[str], int]] = set()

class ExtractedClass:
    def __init__(self, name: str, start_line: int, end_line: int, body_node: Optional[Node] = None):
        self.name = name
        self.start_line = start_line
        self.end_line = end_line
        self.body_node = body_node
        self.methods: List[ExtractedFunction] = []
        self.superclasses: Set[str] = set()

class ExtractedImport:
    def __init__(self, import_type: str, module_path: Optional[str] = None, imported_names: Optional[List[Tuple[str, Optional[str]]]] = None):
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
            if tree.root_node.has_error:
                logger.warning(f"Syntax errors found in file {file_path} during parsing. CKG data might be incomplete.")
            self._extract_entities(tree.root_node, result)
            return result
        except Exception as e:
            logger.error(f"Error parsing file {file_path} with {self.language_name} parser: {e}", exc_info=True)
            return None

    def _extract_entities(self, root_node: Node, result: ParsedFileResult):
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
        self.queries = {
            "imports": self.lang_object.query("""
                (import_statement
                  name: [
                    (dotted_name) @module_path
                    (aliased_import name: (dotted_name) @module_path alias: (identifier) @alias)
                  ]
                ) @import_direct_statement

                (import_from_statement
                  module_name: (dotted_name)? @from_module_path
                  name: [
                    (wildcard_import) @wildcard
                    (dotted_name) @imported_name
                    (aliased_import name: (dotted_name) @imported_name alias: (identifier) @imported_alias)
                  ]
                ) @import_from_statement
            """),
            "classes": self.lang_object.query("""
                (class_definition
                    name: (identifier) @class.name
                    superclasses: (argument_list . (_) @superclass)?
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
                        (identifier) @func_name_direct
                        (attribute object: (identifier) @obj_name attribute: (identifier) @method_name)
                        (attribute object: (call) @chained_call_obj attribute: (identifier) @method_name)
                        (attribute object: (subscript) @subscript_obj attribute: (identifier) @method_name)
                        (attribute object: (attribute) @nested_attr_obj attribute: (identifier) @method_name)
                    ]
                    arguments: (_)? @arguments
                ) @call_expression
            """)
        }
        logger.info("PythonParser initialized.")

    def _process_match_item(self, query_obj: Query, match_as_tuple: Any,
                                main_capture_names_without_at: List[str]) -> Tuple[Optional[Node], Dict[str, List[Node]]]:
        """
        Helper to process captures from a match item.
        Assumes match_as_tuple is a tuple: (pattern_index, dict_of_captures).
        dict_of_captures maps capture_name (e.g. "module_path" or "@module_path") to List[Node] or a single Node.
        main_capture_names_without_at should be like ["import_direct_statement"] (no '@').
        Returns:
            - main_node: The first node associated with any name in main_capture_names_without_at.
            - validated_captures_map: A dict where keys are capture names *without* '@' prefix,
                                      and values are always List[Node].
        """
        if not (isinstance(match_as_tuple, tuple) and len(match_as_tuple) == 2):
            logger.warning(f"CKG Parser: Item from 'matches' iterator is not a 2-element tuple: type {type(match_as_tuple)}, len {len(match_as_tuple) if isinstance(match_as_tuple, tuple) else 'N/A'}. Skipping.")
            return None, {}

        _pattern_index = match_as_tuple[0]
        captures_map_from_iterator = match_as_tuple[1]

        if not isinstance(captures_map_from_iterator, dict):
            logger.error(f"CKG Parser: Second element of match tuple is not a dict as expected, got {type(captures_map_from_iterator)}. This suggests an issue with tree-sitter's output interpretation. Skipping match.")
            return None, {}

        main_node: Optional[Node] = None
        validated_captures_map: Dict[str, List[Node]] = {}

        for raw_capture_key, captured_value in captures_map_from_iterator.items():
            if not isinstance(raw_capture_key, str):
                logger.warning(f"CKG Parser: Invalid capture key type '{type(raw_capture_key)}'. Expected string. Skipping.")
                continue

            nodes_list_for_key: List[Node] = []
            if isinstance(captured_value, Node): # If it's a single Node, wrap it in a list
                nodes_list_for_key = [captured_value]
            elif isinstance(captured_value, list) and all(isinstance(n, Node) for n in captured_value): # If it's already a list of Nodes
                nodes_list_for_key = captured_value
            else: # Unexpected type for captured_value
                logger.warning(f"CKG Parser: Value for capture key '{raw_capture_key}' is not a Node or List[Node]. Got type {type(captured_value)}. Skipping this capture entry.")
                continue 
            
            normalized_key = raw_capture_key[1:] if raw_capture_key.startswith('@') else raw_capture_key
            validated_captures_map[normalized_key] = nodes_list_for_key
            
            if main_capture_names_without_at and normalized_key in main_capture_names_without_at:
                if nodes_list_for_key: 
                    if main_node is None:
                        main_node = nodes_list_for_key[0]
        
        if main_capture_names_without_at and not main_node:
             logger.debug(f"CKG Parser: Main node not found for specified main_capture_names: {main_capture_names_without_at} in captures: {list(validated_captures_map.keys())}")

        return main_node, validated_captures_map

    def _extract_imports(self, root_node: Node, result: ParsedFileResult):
        query_obj = self.queries["imports"]
        processed_statement_node_ids = set()

        for match_as_tuple in query_obj.matches(root_node):
            main_statement_node, captures_dict = self._process_match_item(
                query_obj, match_as_tuple, ["import_direct_statement", "import_from_statement"]
            )
            if not main_statement_node: continue
            if main_statement_node.id in processed_statement_node_ids: continue
            processed_statement_node_ids.add(main_statement_node.id)

            if main_statement_node.type == "import_statement":
                module_path_nodes = captures_dict.get("module_path", [])
                alias_nodes = captures_dict.get("alias", [])
                if module_path_nodes: # Ensure list is not empty
                    module_path_text = self._get_node_text(module_path_nodes[0])
                    alias_text = self._get_node_text(alias_nodes[0]) if alias_nodes else None
                    if alias_text and module_path_text:
                         result.imports.append(ExtractedImport("direct_alias", module_path_text, [(module_path_text, alias_text)]))
                    elif module_path_text:
                        result.imports.append(ExtractedImport("direct", module_path_text, [(module_path_text, None)]))

            elif main_statement_node.type == "import_from_statement":
                from_module_path_node_list = captures_dict.get("from_module_path", [])
                from_module_path_text = self._get_node_text(from_module_path_node_list[0]) if from_module_path_node_list else None
                
                if "wildcard" in captures_dict:
                    result.imports.append(ExtractedImport("from_wildcard", from_module_path_text, [("*", None)]))
                else:
                    imported_items_from: List[Tuple[str, Optional[str]]] = []
                    name_field_container_node = main_statement_node.child_by_field_name('name')
                    if name_field_container_node:
                        for item_node in name_field_container_node.named_children:
                            if item_node.type == "dotted_name":
                                imported_name = self._get_node_text(item_node)
                                if imported_name: imported_items_from.append((imported_name, None))
                            elif item_node.type == "aliased_import":
                                original_name_node = item_node.child_by_field_name("name")
                                alias_node = item_node.child_by_field_name("alias")
                                if original_name_node and alias_node:
                                    original_name, alias_name = self._get_node_text(original_name_node), self._get_node_text(alias_node)
                                    if original_name: imported_items_from.append((original_name, alias_name))
                    if imported_items_from:
                         result.imports.append(ExtractedImport("from", from_module_path_text, imported_items_from))

    def _extract_calls(self, scope_node: Node, current_owner_entity: ExtractedFunction, result: ParsedFileResult):
        if not scope_node: return
        query_obj_calls = self.queries["calls"]
        for match_as_tuple in query_obj_calls.matches(scope_node):
            call_expression_node, captures_dict = self._process_match_item(
                query_obj_calls, match_as_tuple, ["call_expression"]
            )
            if not call_expression_node: continue

            call_name_node_list = captures_dict.get("func_name_direct", [])
            obj_name_node_list = captures_dict.get("obj_name", [])
            method_name_node_list = captures_dict.get("method_name", [])

            call_name_node = call_name_node_list[0] if call_name_node_list else None
            obj_name_node = obj_name_node_list[0] if obj_name_node_list else None
            method_name_node = method_name_node_list[0] if method_name_node_list else None
            
            call_type, called_name_str, base_object_name_str = "unknown", None, None
            if method_name_node:
                call_type, called_name_str = "method", self._get_node_text(method_name_node)
                if obj_name_node: base_object_name_str = self._get_node_text(obj_name_node)
            elif call_name_node: 
                call_type, called_name_str = "direct", self._get_node_text(call_name_node)
            
            if called_name_str:
                current_owner_entity.calls.add(
                    (called_name_str, base_object_name_str, call_type, self._get_line_number(call_expression_node))
                )

    def _extract_functions_and_methods(self, scope_node: Node, result: ParsedFileResult, current_class_obj: Optional[ExtractedClass] = None):
        if not scope_node: return
        query_obj_funcs = self.queries["functions_and_methods"]
        for match_as_tuple in query_obj_funcs.matches(scope_node):
            func_def_node, captures_dict = self._process_match_item(
                query_obj_funcs, match_as_tuple, ["function.definition"]
            )
            if not func_def_node: continue

            is_valid_scope = False
            if current_class_obj:
                if func_def_node.parent == scope_node: is_valid_scope = True
            else: 
                if func_def_node.parent == scope_node or \
                   (func_def_node.parent and func_def_node.parent.type == 'block' and func_def_node.parent.parent == scope_node):
                    is_valid_scope = True
            if not is_valid_scope: continue

            func_name_node_list = captures_dict.get("function.name", [])
            func_name = self._get_node_text(func_name_node_list[0]) if func_name_node_list else None

            params_node_list = captures_dict.get("function.parameters", [])
            params_str = self._get_node_text(params_node_list[0]) if params_node_list else ""
            
            return_type_node_list = captures_dict.get("function.return_type", [])
            return_type_str = self._get_node_text(return_type_node_list[0]) if return_type_node_list else None
            
            func_body_node_list = captures_dict.get("function.body", [])
            func_body_node = func_body_node_list[0] if func_body_node_list else None
            
            if func_name:
                signature = f"{params_str}" + (f" -> {return_type_str}" if return_type_str else "")
                func_obj = ExtractedFunction(
                    name=func_name, start_line=self._get_line_number(func_def_node),
                    end_line=self._get_end_line_number(func_def_node), signature=signature.strip(),
                    parameters_str=params_str.strip(), class_name=current_class_obj.name if current_class_obj else None,
                    body_node=func_body_node
                )
                self._extract_calls(func_body_node, func_obj, result)
                if current_class_obj: current_class_obj.methods.append(func_obj)
                else: result.functions.append(func_obj)

    def _extract_classes(self, root_node: Node, result: ParsedFileResult):
        query_obj_classes = self.queries["classes"]
        for match_as_tuple in query_obj_classes.matches(root_node):
            class_def_node, captures_dict = self._process_match_item(
                query_obj_classes, match_as_tuple, ["class.definition"]
            )
            if not class_def_node: continue

            class_name_node_list = captures_dict.get("class.name", [])
            class_name = self._get_node_text(class_name_node_list[0]) if class_name_node_list else None
            
            class_body_node_list = captures_dict.get("class.body", [])
            class_body_node = class_body_node_list[0] if class_body_node_list else None
            
            superclasses_set: Set[str] = set()
            for sc_node in captures_dict.get("superclass", []): # Iterates list of nodes for "superclass"
                sc_text = self._get_node_text(sc_node)
                if sc_text: superclasses_set.add(sc_text)
            
            if class_name and class_body_node:
                class_obj = ExtractedClass(
                    name=class_name, start_line=self._get_line_number(class_def_node),
                    end_line=self._get_end_line_number(class_def_node), body_node=class_body_node
                )
                class_obj.superclasses = superclasses_set
                self._extract_functions_and_methods(class_body_node, result, class_obj)
                result.classes.append(class_obj)

    def _extract_entities(self, root_node: Node, result: ParsedFileResult):
        self._extract_imports(root_node, result) 
        self._extract_classes(root_node, result)
        self._extract_functions_and_methods(root_node, result, current_class_obj=None)
        
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
    else:
        logger.warning(f"No specific parser class implemented for language: '{language}'. Tree-sitter grammar might be available but entities won't be extracted.")
    
    if parser_instance:
        _parsers_cache[language_key] = parser_instance
    return parser_instance