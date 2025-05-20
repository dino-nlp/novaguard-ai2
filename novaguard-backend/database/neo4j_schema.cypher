// novaguard-backend/database/neo4j_schema.cypher

// === Constraints for Uniqueness ===
// Giúp đảm bảo không có node trùng lặp dựa trên các thuộc tính quan trọng.
// Tên constraint nên là duy nhất.

// Project Node
CREATE CONSTRAINT project_graph_id_unique IF NOT EXISTS
FOR (p:Project) REQUIRE p.graph_id IS UNIQUE;

// File Node
CREATE CONSTRAINT file_composite_id_unique IF NOT EXISTS
FOR (f:File) REQUIRE f.composite_id IS UNIQUE;

// Class Node
CREATE CONSTRAINT class_composite_id_unique IF NOT EXISTS
FOR (c:Class) REQUIRE c.composite_id IS UNIQUE;

// Function Node (bao gồm cả Method vì Method cũng có label Function)
CREATE CONSTRAINT function_composite_id_unique IF NOT EXISTS
FOR (fn:Function) REQUIRE fn.composite_id IS UNIQUE;

// Module Node
// Một module được xác định duy nhất bởi project và đường dẫn của nó.
// Sử dụng IS NODE KEY cho các thuộc tính kết hợp.
CREATE CONSTRAINT module_project_path_key IF NOT EXISTS
FOR (m:Module) REQUIRE (m.project_graph_id, m.path) IS NODE KEY;


// === Indexes for Performance ===
// Giúp tăng tốc độ truy vấn các node dựa trên các thuộc tính thường được tìm kiếm.

// Project Node
CREATE INDEX project_novaguard_id_idx IF NOT EXISTS
FOR (p:Project) ON (p.novaguard_id);

// File Node
CREATE INDEX file_project_path_idx IF NOT EXISTS
FOR (f:File) ON (f.project_graph_id, f.path);

// Class Node
CREATE INDEX class_project_name_idx IF NOT EXISTS
FOR (c:Class) ON (c.project_graph_id, c.name);

// Function Node
CREATE INDEX function_project_name_idx IF NOT EXISTS
FOR (fn:Function) ON (fn.project_graph_id, fn.name);

CREATE INDEX function_file_path_idx IF NOT EXISTS
FOR (fn:Function) ON (fn.file_path); // Nếu thường xuyên tìm function theo file

// General name index (có thể hữu ích nếu tìm kiếm theo tên không kèm project_graph_id)
// Cân nhắc kỹ vì index quá nhiều cũng không tốt.
// CREATE INDEX generic_name_idx IF NOT EXISTS
// FOR (n) ON (n.name);

// Index cho tìm kiếm nhanh các placeholder classes (nếu cần)
CREATE INDEX class_placeholder_idx IF NOT EXISTS
FOR (c:Class) ON (c.placeholder);