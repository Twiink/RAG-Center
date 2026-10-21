from celery import Celery

from rag_center.core.config import get_settings

celery_app = Celery(
    "rag_center",
    broker=get_settings().celery_broker_url,
    backend=get_settings().celery_result_backend,
)

celery_app.conf.update(
    task_track_started=True,
    task_default_queue="indexing",
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    worker_redirect_stdouts=False,
    broker_connection_timeout=3,
    broker_transport_options={"socket_connect_timeout": 3, "socket_timeout": 3},
    imports=["rag_center.modules.indexing.tasks.indexing"],
)


def publish_index_job(topic: str, payload: dict):
    """调度器使用的消息发布函数；消息仅包含 job_id。"""

    # 调度器在线程池投递；shared_task 代理会读取线程局部 current_app，可能退回默认 broker。
    return celery_app.send_task(
        "rag_center.index_document", kwargs={"job_id": payload["job_id"]}, retry=False
    )
