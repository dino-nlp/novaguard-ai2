# novaguard-ai2/scripts/init_neo4j.py
import asyncio
import os
import logging # Thêm logging
from pathlib import Path

# Đảm bảo settings được import đúng cách để lấy NEO4J_URI, USER, PASSWORD
# Giả sử app.core.config và app.core.graph_db đã đúng
from app.core.config import settings
from app.core.graph_db import get_async_neo4j_driver, close_async_neo4j_driver

# Cấu hình logging cơ bản cho script
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Xác định đường dẫn đến file schema một cách an toàn
# Giả sử script này nằm trong thư mục scripts/ và schema nằm trong novaguard-backend/database/
BASE_DIR = Path(__file__).resolve().parent.parent # Thư mục gốc novaguard-ai2/
NEO4J_SCHEMA_FILE = BASE_DIR / "novaguard-backend" / "database" / "neo4j_schema.cypher"

async def apply_neo4j_schema():
    driver = None
    try:
        logger.info(f"Attempting to connect to Neo4j URI: {settings.NEO4J_URI}")
        driver = await get_async_neo4j_driver() # Hàm này đã có verify_connectivity bên trong
        if not driver:
            logger.error("Failed to get Neo4j driver. Aborting schema initialization.")
            return

        if not NEO4J_SCHEMA_FILE.exists():
            logger.error(f"Neo4j schema file not found at: {NEO4J_SCHEMA_FILE}")
            return

        logger.info(f"Applying Neo4j schema from: {NEO4J_SCHEMA_FILE}")
        with open(NEO4J_SCHEMA_FILE, 'r', encoding='utf-8') as f: # Thêm encoding
            cypher_script = f.read()

        # Tách các lệnh Cypher bằng dấu chấm phẩy (;)
        # và loại bỏ các dòng comment // hoặc --, các dòng rỗng
        raw_commands = cypher_script.split(';')
        commands_to_execute = []
        for cmd_raw in raw_commands:
            cmd_lines = []
            for line in cmd_raw.splitlines():
                stripped_line = line.strip()
                if stripped_line and not stripped_line.startswith("//") and not stripped_line.startswith("--"):
                    cmd_lines.append(stripped_line)
            if cmd_lines:
                commands_to_execute.append(" ".join(cmd_lines)) # Nối lại các dòng của một lệnh

        if not commands_to_execute:
            logger.info("No valid Cypher commands found in the schema file.")
            return

        # Sử dụng database mặc định của driver (thường là "neo4j")
        # Hoặc bạn có thể cấu hình cụ thể nếu cần
        db_name_to_use = getattr(driver, 'database', 'neo4j')
        if hasattr(driver, 'default_database'): # Một số phiên bản driver có thể dùng default_database
             db_name_to_use = driver.default_database

        async with driver.session(database=db_name_to_use) as session:
            for i, command in enumerate(commands_to_execute):
                if not command.strip(): # Bỏ qua lệnh rỗng sau khi xử lý
                    continue
                logger.info(f"Executing command {i+1}/{len(commands_to_execute)}: {command[:150]}...")
                try:
                    # Các lệnh CREATE CONSTRAINT/INDEX cần được chạy trong một transaction riêng
                    # hoặc trong một auto-commit transaction (execute_write).
                    # `execute_write` sẽ tự động quản lý transaction.
                    # Một số phiên bản Neo4j yêu cầu các lệnh DDL (như CREATE CONSTRAINT)
                    # phải là lệnh duy nhất trong transaction của chúng.
                    # Chạy từng lệnh trong một execute_write riêng biệt là an toàn nhất.
                    async def run_single_command(tx, single_cmd):
                        await tx.run(single_cmd)

                    await session.execute_write(run_single_command, command)
                    logger.info(f"Command executed successfully: {command[:70]}...")
                except Exception as e:
                    # Neo4j thường có mã lỗi cụ thể cho việc constraint/index đã tồn tại
                    # Ví dụ: Neo.ClientError.Schema.EquivalentSchemaRuleAlreadyExists
                    # Neo.ClientError.Schema.IndexAlreadyExists
                    # Kiểm tra xem có phải lỗi "đã tồn tại" không
                    if "already exists" in str(e).lower() or \
                       "EquivalentSchemaRuleAlreadyExists" in str(e) or \
                       "IndexAlreadyExists" in str(e):
                        logger.warning(f"Skipping command (already applied or equivalent exists): {command[:70]}... Error: {str(e)[:100]}")
                    else:
                        logger.error(f"Error executing command: {command}")
                        logger.error(f"Neo4j Error: {e}", exc_info=False) # Không cần full stack trace nếu chỉ là lỗi Cypher
                        # Quyết định có nên dừng lại không nếu có lỗi nghiêm trọng
                        # raise # Bỏ comment dòng này nếu muốn dừng lại khi có lỗi bất kỳ

        logger.info("Neo4j schema (constraints and indexes) applied successfully or confirmed to exist.")

    except ConnectionError as ce: # Bắt lỗi kết nối cụ thể hơn
        logger.error(f"Neo4j connection error: {ce}")
    except Exception as e:
        logger.error(f"An unexpected error occurred during Neo4j schema initialization: {e}", exc_info=True)
    finally:
        if driver:
            await close_async_neo4j_driver()
            logger.info("Neo4j driver closed.")

if __name__ == "__main__":
    # Để chạy script này độc lập, bạn cần đảm bảo PYTHONPATH được thiết lập đúng
    # để import app.core.config và app.core.graph_db
    # Ví dụ, từ thư mục gốc novaguard-ai2:
    # export PYTHONPATH=$(pwd)/novaguard-backend:$PYTHONPATH
    # python scripts/init_neo4j.py

    # Hoặc đặt các dòng sau vào đầu script (chỉ cho mục đích chạy độc lập từ thư mục scripts):
    # import sys
    # script_dir = Path(__file__).resolve().parent
    # project_root = script_dir.parent
    # backend_app_path = project_root / "novaguard-backend"
    # if str(backend_app_path) not in sys.path:
    #    sys.path.insert(0, str(backend_app_path))

    # In ra PYTHONPATH để kiểm tra (chỉ cho debug)
    # logger.debug(f"PYTHONPATH: {os.getenv('PYTHONPATH')}")
    # logger.debug(f"sys.path: {sys.path}")

    asyncio.run(apply_neo4j_schema())