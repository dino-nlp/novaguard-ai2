import json
import logging
from kafka import KafkaProducer
from kafka.errors import KafkaError

from app.core.config import settings

logger = logging.getLogger(__name__)

_kafka_producer = None

def get_kafka_producer() -> KafkaProducer | None:
    global _kafka_producer
    if _kafka_producer is None:
        try:
            _kafka_producer = KafkaProducer(
                bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS.split(','),
                value_serializer=lambda v: json.dumps(v).encode('utf-8'),
                # request_timeout_ms=10000, # Tăng timeout nếu cần
                # retries=3 # Số lần thử lại nếu gửi lỗi
            )
            logger.info(f"KafkaProducer connected to {settings.KAFKA_BOOTSTRAP_SERVERS}")
        except KafkaError as e:
            logger.error(f"Failed to connect KafkaProducer to {settings.KAFKA_BOOTSTRAP_SERVERS}: {e}")
            _kafka_producer = None # Đảm bảo nó vẫn là None nếu kết nối lỗi
    return _kafka_producer

async def send_pr_analysis_task(task_data: dict) -> bool:
    producer = get_kafka_producer()
    if not producer:
        logger.error("Kafka producer is not available. Cannot send PR analysis task.")
        return False
    
    topic = settings.KAFKA_PR_ANALYSIS_TOPIC
    try:
        # Gửi message. Key có thể là project_id hoặc pr_id để Kafka phân vùng tốt hơn nếu cần
        # message_key = str(task_data.get("pr_analysis_request_id", "default_key")).encode('utf-8')
        future = producer.send(topic, value=task_data) #, key=message_key)
        # Chờ message được gửi (có thể có timeout)
        record_metadata = future.get(timeout=10) # Chờ tối đa 10 giây
        logger.info(
            f"PR analysis task sent to topic '{topic}', "
            f"partition {record_metadata.partition}, offset {record_metadata.offset}"
        )
        producer.flush() # Đảm bảo tất cả message đã được gửi đi
        return True
    except KafkaError as e:
        logger.error(f"Failed to send PR analysis task to Kafka topic '{topic}': {e}")
        return False
    except Exception as e:
        logger.error(f"An unexpected error occurred while sending task to Kafka: {e}")
        return False

# Hàm để đóng producer khi ứng dụng tắt (nếu cần gọi tường minh)
def close_kafka_producer():
    global _kafka_producer
    if _kafka_producer:
        logger.info("Closing KafkaProducer.")
        _kafka_producer.close()
        _kafka_producer = None

# Ví dụ sử dụng (có thể đặt trong một endpoint hoặc service khác sau này)
# async def example_usage():
#     task = {"pr_analysis_request_id": 123, "project_id": 1, "diff_url": "http://example.com/diff"}
#     await send_pr_analysis_task(task)