"""集成测试只访问显式指定且名称以 _test 结尾的隔离数据库。"""

import os
import threading
from http.server import ThreadingHTTPServer
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete

from rag_center.core.config import Settings, settings_context
from rag_center.core.container import active_container
from rag_center.main import create_app
from rag_center.modules.indexing.models.index_job import IndexJob
from rag_center.modules.knowledge_bases.models.knowledge_base import KnowledgeBase
from rag_center.modules.tenants.api_key_crypto import hash_api_key
from rag_center.modules.tenants.models.api_key import ApiKey
from rag_center.modules.tenants.models.tenant import Tenant
from tests.support.model_stub import Handler


@pytest.fixture(scope="session")
def model_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/v1"
    server.shutdown()
    thread.join()
    server.server_close()


@pytest.fixture
async def runtime(model_server, tmp_path):
    url = os.environ.get("RAG_TEST_DATABASE_URL")
    if not url:
        pytest.skip("需要 RAG_TEST_DATABASE_URL 指向隔离数据库")
    if not url.rsplit("/", 1)[-1].endswith("_test"):
        pytest.fail("集成数据库名必须以 _test 结尾")
    redis_url = os.environ.get("RAG_TEST_REDIS_URL", "redis://127.0.0.1:16379/0")
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=url,
        redis_url=redis_url,
        celery_broker_url=redis_url,
        celery_result_backend=redis_url,
        elasticsearch_url=os.environ.get("RAG_TEST_ELASTICSEARCH_URL", "http://127.0.0.1:19200"),
        model_base_url=model_server,
        llm_base_url=model_server,
        model_api_key="protocol-stub",
        log_dir=str(tmp_path / "logs"),
        document_storage_path=str(tmp_path / "uploads"),
        index_job_retry_backoff_seconds=1,
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        container = app.state.container
        with settings_context(settings):
            token = active_container.set(container)
            async with container.session_factory() as session:
                tenant_id = f"test-{uuid4()}"
                session.add(Tenant(id=tenant_id, name="integration", status="active", plan="pro"))
                await session.flush()
                key = f"integration-{uuid4()}"
                session.add(
                    ApiKey(
                        id=str(uuid4()),
                        tenant_id=tenant_id,
                        name="integration",
                        key_hash=hash_api_key(key),
                        key_prefix="test",
                        status="active",
                    )
                )
                await session.commit()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": f"Bearer {key}"},
            ) as client:
                yield container, client, tenant_id
            async with container.session_factory() as session:
                await session.execute(delete(IndexJob).where(IndexJob.tenant_id == tenant_id))
                await session.execute(
                    delete(KnowledgeBase).where(KnowledgeBase.tenant_id == tenant_id)
                )
                await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
                await session.commit()
            active_container.reset(token)
