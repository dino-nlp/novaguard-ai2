# novaguard-ai2/scripts/init_neo4j.py
import asyncio
import os
from pathlib import Path

from app.core.config import settings
from app.core.graph_db import get_async_neo4j_driver, close_async_neo4j_driver


# --- BEGIN DEBUG ---
print(f"DEBUG: Current working directory: {Path.cwd()}")
# Xóa cache của get_settings để đảm bảo nó đọc lại file .env mỗi lần chạy script này (cho mục đích debug)
# Tuy nhiên, settings = get_settings() ở cuối config.py đã được gọi một lần khi module config được import.
# Để chắc chắn, chúng ta có thể tạo một instance Settings mới.
# get_settings.cache_clear() # Xóa cache nếu get_settings được gọi lại
# fresh_settings = Settings() # Tạo instance mới, không qua cache get_settings
# print(f"DEBUG: ACCESS_TOKEN_EXPIRE_MINUTES from fresh_settings: {fresh_settings.ACCESS_TOKEN_EXPIRE_MINUTES} (Type: {type(fresh_settings.ACCESS_TOKEN_EXPIRE_MINUTES)})")
# print(f"DEBUG: TEST_ENV_VAR_INIT_NEO4J from fresh_settings: {getattr(fresh_settings, 'TEST_ENV_VAR_INIT_NEO4J', 'NOT_FOUND')}")

# Cách tốt hơn để kiểm tra là xem instance `settings` đã được import:
print(f"DEBUG: ACCESS_TOKEN_EXPIRE_MINUTES from imported settings: {settings.ACCESS_TOKEN_EXPIRE_MINUTES} (Type: {type(settings.ACCESS_TOKEN_EXPIRE_MINUTES)})")
print(f"DEBUG: TEST_ENV_VAR_INIT_NEO4J from imported settings: {getattr(settings, 'TEST_ENV_VAR_INIT_NEO4J', 'NOT_FOUND')}")
# --- END DEBUG ---

NEO4J_SCHEMA_FILE = Path(__file__).parent.parent / "novaguard-backend" / "database" / "neo4j_schema.cypher"

async def apply_neo4j_schema():
    driver = None
    try:
        driver = await get_async_neo4j_driver()
        if not driver:
            print("Failed to get Neo4j driver. Aborting schema initialization.")
            return

        if not NEO4J_SCHEMA_FILE.exists():
            print(f"Neo4j schema file not found at: {NEO4J_SCHEMA_FILE}")
            return

        print(f"Applying Neo4j schema from: {NEO4J_SCHEMA_FILE}")
        with open(NEO4J_SCHEMA_FILE, 'r') as f:
            cypher_script = f.read()

        # Tách các lệnh Cypher bằng dấu chấm phẩy (;) nếu có nhiều lệnh
        # và loại bỏ các dòng comment
        commands = [
            cmd.strip() for cmd in cypher_script.split(';')
            if cmd.strip() and not cmd.strip().startswith("//")
        ]

        async with driver.session(database="neo4j") as session:
            for i, command in enumerate(commands):
                if not command: continue # Bỏ qua lệnh rỗng
                print(f"Executing command {i+1}/{len(commands)}: {command[:100]}...")
                try:
                    # Sử dụng write_transaction cho các lệnh CREATE CONSTRAINT/INDEX
                    await session.execute_write(lambda tx: tx.run(command))
                    print(f"Command executed successfully.")
                except Exception as e:
                    print(f"Error executing command: {command}")
                    print(f"Error: {e}")
                    # Quyết định có dừng lại không nếu có lỗi
                    # return

        print("Neo4j schema applied successfully.")

    except Exception as e:
        print(f"An error occurred during Neo4j schema initialization: {e}")
    finally:
        if driver:
            await close_async_neo4j_driver()

if __name__ == "__main__":
    asyncio.run(apply_neo4j_schema())
    

# Từ thư mục gốc novaguard-ai2
# export PYTHONPATH=$(pwd)/novaguard-backend:$PYTHONPATH (nếu chưa set)
# python scripts/init_neo4j.py
# NEO4J_URI="neo4j://localhost:7687" NEO4J_USER="neo4j" NEO4J_PASSWORD="yourStrongPassword" python scripts/init_neo4j.py