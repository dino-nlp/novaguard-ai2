# novaguard-backend/app/core/graph_db.py
from neo4j import GraphDatabase, Driver, AsyncGraphDatabase, AsyncDriver # Thêm Async
import logging
from app.core.config import settings
from typing import Optional


logger = logging.getLogger(__name__)

_async_driver: Optional[AsyncDriver] = None

async def get_async_neo4j_driver() -> AsyncDriver:
    global _async_driver
    if _async_driver is None:
        try:
            logger.info(f"Attempting to create Neo4j AsyncDriver for URI: {settings.NEO4J_URI}")
            _async_driver = AsyncGraphDatabase.driver(
                settings.NEO4J_URI,
                auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD)
            )
            # Kiểm tra kết nối cơ bản (tùy chọn, nhưng hữu ích)
            await _async_driver.verify_connectivity()
            logger.info("Neo4j AsyncDriver created and connectivity verified.")
        except Exception as e:
            logger.exception(f"Failed to create or verify Neo4j AsyncDriver: {e}")
            # Reset _async_driver để thử lại lần sau nếu cần, hoặc raise lỗi nghiêm trọng
            _async_driver = None
            raise  # Hoặc xử lý lỗi một cách phù hợp
    return _async_driver

async def close_async_neo4j_driver():
    global _async_driver
    if _async_driver is not None:
        logger.info("Closing Neo4j AsyncDriver.")
        await _async_driver.close()
        _async_driver = None

# Dependency cho FastAPI (nếu bạn cần inject session vào API routes sau này)
# async def get_neo4j_async_session():
#     driver = await get_async_neo4j_driver()
#     if not driver:
#         raise HTTPException(status_code=503, detail="Graph database not available.")
#     async with driver.session() as session:
#         yield session

# Ví dụ chạy một query đơn giản (dùng trong worker hoặc service)
async def run_cypher_query(query: str, parameters: Optional[dict] = None):
    driver = await get_async_neo4j_driver()
    if not driver:
        logger.error("Cannot run Cypher query: Neo4j driver not available.")
        return None # Hoặc raise exception

    records = []
    summary = None
    async with driver.session() as session:
        try:
            results = await session.run(query, parameters)
            records = [record async for record in results] # Thu thập records
            summary = await results.consume() # Lấy summary (thông tin về query)
        except Exception as e:
            logger.exception(f"Error running Cypher query '{query[:100]}...': {e}")
            raise # Re-raise để bên gọi xử lý
    return records, summary

# Thêm event handler cho FastAPI để đóng driver khi shutdown (trong main.py)
# @app.on_event("shutdown")
# async def shutdown_neo4j_driver():
#     await close_async_neo4j_driver()