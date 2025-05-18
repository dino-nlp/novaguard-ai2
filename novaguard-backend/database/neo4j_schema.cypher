// novaguard-backend/database/neo4j_schema.cypher

// Constraints for :Project
CREATE CONSTRAINT project_graph_id_unique IF NOT EXISTS FOR (p:Project) REQUIRE p.graph_id IS UNIQUE;

// Constraints and Indexes for :File
// Tạo một composite key để đảm bảo tính duy nhất của file trong một project
// Bạn sẽ cần tạo property này trong CKGBuilder khi tạo node File
CREATE CONSTRAINT file_composite_id_unique IF NOT EXISTS FOR (f:File) REQUIRE f.composite_id IS UNIQUE;
CREATE INDEX file_path_index IF NOT EXISTS FOR (f:File) ON (f.path);
CREATE INDEX file_project_graph_id_index IF NOT EXISTS FOR (f:File) ON (f.project_graph_id);
// CREATE INDEX file_language_index IF NOT EXISTS FOR (f:File) ON (f.language); // Cân nhắc nếu query theo language nhiều

// Constraints and Indexes for :Class
// Tương tự, composite_id cho class (ví dụ: project_graph_id + file_path + class_name + start_line)
CREATE CONSTRAINT class_composite_id_unique IF NOT EXISTS FOR (c:Class) REQUIRE c.composite_id IS UNIQUE;
CREATE INDEX class_name_index IF NOT EXISTS FOR (c:Class) ON (c.name);
CREATE INDEX class_file_path_index IF NOT EXISTS FOR (c:Class) ON (c.file_path); // Nếu query class theo file path
CREATE INDEX class_project_graph_id_index IF NOT EXISTS FOR (c:Class) ON (c.project_graph_id);

// Constraints and Indexes for :Function (bao gồm cả Method)
// Composite_id cho function (ví dụ: project_graph_id + file_path + function_name + start_line)
// Nếu Function và Method là label riêng biệt, cần định nghĩa riêng.
// Hiện tại bạn dùng `MERGE (m:Method:Function ...)` nên Function cũng áp dụng cho Method.
CREATE CONSTRAINT function_composite_id_unique IF NOT EXISTS FOR (fn:Function) REQUIRE fn.composite_id IS UNIQUE;
CREATE INDEX function_name_index IF NOT EXISTS FOR (fn:Function) ON (fn.name);
CREATE INDEX function_file_path_index IF NOT EXISTS FOR (fn:Function) ON (fn.file_path);
CREATE INDEX function_project_graph_id_index IF NOT EXISTS FOR (fn:Function) ON (fn.project_graph_id);
// CREATE INDEX function_class_name_index IF NOT EXISTS FOR (fn:Function) ON (fn.class_name); // Nếu query function theo class_name (cho method)

// Indexes for :Module (nếu bạn sẽ query Module thường xuyên)
CREATE INDEX module_path_index IF NOT EXISTS FOR (m:Module) ON (m.path);
CREATE INDEX module_project_graph_id_index IF NOT EXISTS FOR (m:Module) ON (m.project_graph_id);

// Thêm các index cho các relationship properties nếu bạn filter dựa trên chúng
// Ví dụ: CREATE INDEX rel_calls_type_index IF NOT EXISTS FOR ()-[r:CALLS]-() ON (r.type);