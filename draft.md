## Cách chạy init_neo4j.py (sau khi đã có file .env với thông tin Neo4j):

1. Đảm bảo container Neo4j đang chạy: docker compose up -d neo4j_db
2. Từ thư mục gốc novaguard-ai2/:

```bash
# Thiết lập PYTHONPATH nếu chạy lần đầu hoặc trong môi trường mới
export PYTHONPATH=$(pwd)/novaguard-backend:$PYTHONPATH
# Chạy script
python scripts/init_neo4j.py
```

Hoặc nếu bạn muốn truyền biến môi trường trực tiếp (ghi đè .env cho lần chạy này):

```bash
NEO4J_URI="neo4j://localhost:7687" NEO4J_USER="neo4j" NEO4J_PASSWORD="yourStrongPassword" \
PYTHONPATH=$(pwd)/novaguard-backend:$PYTHONPATH python scripts/init_neo4j.py
```