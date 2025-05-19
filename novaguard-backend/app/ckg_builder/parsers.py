# novaguard-ai2/novaguard-backend/app/ckg_builder/parsers.py
import logging
from typing import List, Dict, Any, Tuple, Optional, Set
from tree_sitter import Language, Parser, Node, Query
from tree_sitter_languages import get_language

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

    def _process_match_item(self, query_obj: Query, match_item_from_iterator,
                                main_capture_names: List[str]) -> Tuple[Optional[Node], Dict[str, List[Node]]]:
        """
        Helper to process captures from a match item.
        Assumes match_item_from_iterator is a tuple: (pattern_index, list_of_Capture_objects).
        """
        if not (isinstance(match_item_from_iterator, tuple) and len(match_item_from_iterator) >= 1): # Can be 1 if no captures
            logger.warning(f"CKG Parser: Unexpected item from 'matches' iterator: type {type(match_item_from_iterator)}. Skipping.")
            return None, {}
        
        # _pattern_index = match_item_from_iterator[0] # We might not always need pattern_index
        
        # If the tuple only has 1 element (pattern_index), it means no captures for that pattern in this match.
        if len(match_item_from_iterator) == 1 :
             list_of_capture_objects = []
        else:
            list_of_capture_objects = match_item_from_iterator[1]


        if not isinstance(list_of_capture_objects, (list, tuple)):
            logger.warning(f"CKG Parser: Expected list/tuple of captures, got {type(list_of_capture_objects)}. Skipping match.")
            return None, {}

        main_node = None
        captures_dict = {}

        for capture_obj in list_of_capture_objects: # Iterating over list of Capture objects
            if not (hasattr(capture_obj, 'node') and hasattr(capture_obj, 'index')):
                logger.warning(f"CKG Parser: Unexpected capture object type {type(capture_obj)} in captures list. Attributes 'node' or 'index' missing. Skipping capture.")
                continue
                
            node = capture_obj.node
            capture_name_id = capture_obj.index # This is the integer ID for the capture name
            capture_name_str = query_obj.capture_name_for_id(capture_name_id)
            
            captures_dict.setdefault(capture_name_str, []).append(node)
            if capture_name_str in main_capture_names:
                main_node = node
        
        return main_node, captures_dict

    def _extract_imports(self, root_node: Node, result: ParsedFileResult):
        query_obj = self.queries["imports"]
        processed_statement_node_ids = set()

        for match_as_tuple in query_obj.matches(root_node):
            main_statement_node, captures_in_this_match_dict = self._process_match_item(
                query_obj, match_as_tuple, ["import_direct_statement", "import_from_statement"]
            )
            
            if not main_statement_node:
                # _process_match_item already logs warnings if format is unexpected
                # Log specific to import if needed
                # logger.debug("CKG Import: Main statement node not found in match. Skipping.")
                continue

            if main_statement_node.id in processed_statement_node_ids:
                continue
            processed_statement_node_ids.add(main_statement_node.id)

            if main_statement_node.type == "import_statement":
                module_path_nodes = captures_in_this_match_dict.get("module_path", [])
                alias_nodes = captures_in_this_match_dict.get("alias", [])
                if module_path_nodes:
                    module_path_text = self._get_node_text(module_path_nodes[0])
                    alias_text = self._get_node_text(alias_nodes[0]) if alias_nodes else None
                    if alias_text and module_path_text:
                         result.imports.append(ExtractedImport("direct_alias", module_path_text, [(module_path_text, alias_text)]))
                    elif module_path_text:
                        result.imports.append(ExtractedImport("direct", module_path_text, [(module_path_text, None)]))

            elif main_statement_node.type == "import_from_statement":
                from_module_path_text = self._get_node_text(captures_in_this_match_dict.get("from_module_path", [None])[0])
                if "wildcard" in captures_in_this_match_dict:
                    result.imports.append(ExtractedImport("from_wildcard", from_module_path_text, [("*", None)]))
                else:
                    imported_items_from: List[Tuple[str, Optional[str]]] = []
                    # The 'name' field of import_from_statement contains the imported items.
                    # These items are captured by @imported_name and @imported_alias by the query.
                    # We need to correctly pair them if they were part of an aliased_import within the from statement.
                    # The main_statement_node is (import_from_statement). Its child for 'name' holds the items.
                    name_field_container_node = main_statement_node.child_by_field_name('name')
                    if name_field_container_node:
                        for item_node in name_field_container_node.named_children: # Iterate over actual (dotted_name) or (aliased_import)
                            if item_node.type == "dotted_name":
                                imported_name = self._get_node_text(item_node)
                                if imported_name:
                                    imported_items_from.append((imported_name, None))
                            elif item_node.type == "aliased_import":
                                original_name_node = item_node.child_by_field_name("name")
                                alias_node = item_node.child_by_field_name("alias")
                                if original_name_node and alias_node:
                                    original_name = self._get_node_text(original_name_node)
                                    alias_name = self._get_node_text(alias_node)
                                    if original_name:
                                        imported_items_from.append((original_name, alias_name))
                    if imported_items_from:
                         result.imports.append(ExtractedImport("from", from_module_path_text, imported_items_from))

    def _extract_calls(self, scope_node: Node, current_owner_entity: ExtractedFunction, result: ParsedFileResult):
        if not scope_node: return
        query_obj_calls = self.queries["calls"]
        for match_as_tuple in query_obj_calls.matches(scope_node):
            call_expression_node, captures_in_this_call_match = self._process_match_item(
                query_obj_calls, match_as_tuple, ["call_expression"]
            )
            if not call_expression_node: continue

            call_name_node = captures_in_this_call_match.get("func_name_direct", [None])[0]
            obj_name_node = captures_in_this_call_match.get("obj_name", [None])[0]
            method_name_node = captures_in_this_call_match.get("method_name", [None])[0]
            
            call_type = "unknown"
            called_name_str: Optional[str] = None
            base_object_name_str: Optional[str] = None

            if method_name_node:
                call_type = "method"
                called_name_str = self._get_node_text(method_name_node)
                if obj_name_node: 
                    base_object_name_str = self._get_node_text(obj_name_node)
            elif call_name_node: 
                call_type = "direct"
                called_name_str = self._get_node_text(call_name_node)
            
            if called_name_str:
                call_site_line = self._get_line_number(call_expression_node)
                current_owner_entity.calls.add(
                    (called_name_str, base_object_name_str, call_type, call_site_line)
                )

    def _extract_functions_and_methods(self, scope_node: Node, result: ParsedFileResult, current_class_obj: Optional[ExtractedClass] = None):
        if not scope_node: return
        query_obj_funcs = self.queries["functions_and_methods"]

        for match_as_tuple in query_obj_funcs.matches(scope_node):
            func_def_node, captures_in_this_func_match = self._process_match_item(
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

            func_name = self._get_node_text(captures_in_this_func_match.get("function.name", [None])[0])
            params_str_node = captures_in_this_func_match.get("function.parameters", [None])[0]
            params_str = self._get_node_text(params_str_node) if params_str_node else ""
            
            return_type_node = captures_in_this_func_match.get("function.return_type", [None])[0]
            return_type_str = self._get_node_text(return_type_node) if return_type_node else None
            
            func_body_node = captures_in_this_func_match.get("function.body", [None])[0]
            
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
            class_def_node, captures_in_this_class_match = self._process_match_item(
                query_obj_classes, match_as_tuple, ["class.definition"]
            )
            if not class_def_node: continue

            class_name = self._get_node_text(captures_in_this_class_match.get("class.name", [None])[0])
            class_body_node = captures_in_this_class_match.get("class.body", [None])[0]
            superclasses_set: Set[str] = set()
            for sc_node in captures_in_this_class_match.get("superclass", []):
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