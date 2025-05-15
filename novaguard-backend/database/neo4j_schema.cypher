// novaguard-backend/database/neo4j_schema.cypher

// Constraints (đảm bảo tính duy nhất và tạo index tự động cho property đó)
CREATE CONSTRAINT project_id_path_unique IF NOT EXISTS FOR (f:File) REQUIRE (f.project_id, f.path) IS UNIQUE;
CREATE CONSTRAINT project_id_module_path_unique IF NOT EXISTS FOR (m:Module) REQUIRE (m.project_id, m.path) IS UNIQUE;
// Function và Class có thể trùng tên trong các file khác nhau, nhưng trong cùng 1 file và project_id thì nên là duy nhất (hoặc kết hợp thêm start_line)
CREATE CONSTRAINT project_id_file_func_name_line_unique IF NOT EXISTS FOR (fn:Function) REQUIRE (fn.project_id, fn.file_path, fn.name, fn.start_line) IS UNIQUE;
CREATE CONSTRAINT project_id_file_class_name_line_unique IF NOT EXISTS FOR (c:Class) REQUIRE (c.project_id, c.file_path, c.name, c.start_line) IS UNIQUE;


// Indexes (tạo index cho các property thường xuyên được query để tăng tốc độ)
CREATE INDEX file_path_idx IF NOT EXISTS FOR (f:File) ON (f.path);
CREATE INDEX file_project_id_idx IF NOT EXISTS FOR (f:File) ON (f.project_id);

CREATE INDEX function_name_idx IF NOT EXISTS FOR (fn:Function) ON (fn.name);
CREATE INDEX function_file_path_idx IF NOT EXISTS FOR (fn:Function) ON (fn.file_path);
CREATE INDEX function_project_id_idx IF NOT EXISTS FOR (fn:Function) ON (fn.project_id);

CREATE INDEX class_name_idx IF NOT EXISTS FOR (c:Class) ON (c.name);
CREATE INDEX class_file_path_idx IF NOT EXISTS FOR (c:Class) ON (c.file_path);
CREATE INDEX class_project_id_idx IF NOT EXISTS FOR (c:Class) ON (c.project_id);

CREATE INDEX module_name_idx IF NOT EXISTS FOR (m:Module) ON (m.name);
CREATE INDEX module_project_id_idx IF NOT EXISTS FOR (m:Module) ON (m.project_id);

// Ghi chú:
// - Node Labels cơ bản: File, Function, Class, Module
// - Relationship Types cơ bản: DEFINED_IN, CALLS, IMPORTS, INHERITS_FROM, IMPLEMENTS (cho interfaces)
// Các labels và relationships này sẽ được tạo động bởi CodeGraphBuilderNode.
// Script này chủ yếu là để thiết lập constraints và indexes.

// Ví dụ về cách bạn có thể kiểm tra sau khi chạy:
// SHOW CONSTRAINTS;
// SHOW INDEXES;