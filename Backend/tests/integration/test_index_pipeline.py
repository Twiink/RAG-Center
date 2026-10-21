import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from celery import Celery
from sqlalchemy import func, select, text, update

from rag_center.modules.documents.models.document import Document
from rag_center.modules.indexing.models.chunk import Chunk
from rag_center.modules.indexing.models.index_job import IndexJob, IndexOutbox
from rag_center.modules.indexing.services.job_executor import (
    IndexJobLeaseLost,
    claim_index_job,
    execute_index_job,
    fail_index_job,
    lock_valid_lease,
    recover_expired_index_jobs,
)
from rag_center.modules.indexing.services.outbox import (
    publish_pending_index_outbox,
    repair_queued_deliveries,
)
from rag_center.modules.indexing.services.task_dispatch import run_index_job

pytestmark = pytest.mark.integration


async def upload(runtime, *, content="退款规则：签收后七天内可以退货。", file=False):
    _, client, _ = runtime
    kb = await client.post("/api/v1/knowledge-bases", json={"name": "规则库"})
    assert kb.status_code == 201, kb.text
    kb_id = kb.json()["data"]["kb_id"]
    path = f"/api/v1/knowledge-bases/{kb_id}/documents"
    response = (
        await client.post(path, files={"file": ("rules.md", content.encode(), "text/markdown")})
        if file
        else await client.post(path, json={"title": "规则", "content": content})
    )
    assert response.status_code == 202, response.text
    assert response.json()["request_id"] == response.headers["X-Request-ID"]
    return kb_id, response.json()["data"]


@pytest.mark.parametrize("file", [False, True])
async def test_upload_index_retrieve_and_atomic_publication(runtime, file):
    container, client, _ = runtime
    kb_id, data = await upload(runtime, file=file)
    async with container.session_factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(IndexOutbox)
                .where(IndexOutbox.job_id == data["job_id"])
            )
            == 1
        )
        result = await run_index_job(
            session, data["job_id"], container.settings, container=container
        )
        assert result == "succeeded"
        count = await session.scalar(
            select(func.count()).select_from(Chunk).where(Chunk.document_id == data["document_id"])
        )
        assert count > 0
    for mode in ["vector", "bm25", "hybrid"]:
        # ES refresh 由写入 Provider 确认，检索不依赖任意 sleep。
        response = await client.post(
            "/api/v1/rag/retrieve",
            json={
                "kb_id": kb_id,
                "user_id": "test",
                "query": "退货规则",
                "profile": "custom",
                "retrieval_options": {"mode": mode},
                "rerank_options": {"enabled": False},
                "query_options": {"enabled": False},
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["retrieved_chunks"], response.text
    duplicate = await client.post(f"/api/v1/documents/{data['document_id']}/index-jobs")
    assert duplicate.status_code == 202
    conflict = await client.post(f"/api/v1/documents/{data['document_id']}/index-jobs")
    assert conflict.status_code == 409
    # 新任务完成前，已发布产物仍然可读。
    async with container.session_factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Chunk)
                .where(Chunk.document_id == data["document_id"])
            )
            == count
        )
        assert (
            await run_index_job(
                session, duplicate.json()["data"]["job_id"], container.settings, container=container
            )
            == "succeeded"
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Chunk)
                .where(Chunk.document_id == data["document_id"])
            )
            == count
        )
    assert (await client.delete(f"/api/v1/documents/{data['document_id']}")).status_code == 204


async def test_tenant_isolation_and_parameter_validation(runtime):
    _, client, _ = runtime
    kb_id, data = await upload(runtime)
    response = await client.post(f"/api/v1/knowledge-bases/{kb_id}/active-index-version", json={})
    assert response.status_code == 405
    response = await client.put(
        f"/api/v1/knowledge-bases/{kb_id}/active-index-version", json={"version": ""}
    )
    assert response.status_code == 422
    from rag_center.api.v1.deps import ApiDependencies
    from rag_center.core.auth import TenantContext

    async def other_tenant():
        return TenantContext("another-tenant", "other", None, None, None)

    app = client._transport.app
    app.dependency_overrides[ApiDependencies.tenant] = other_tenant
    try:
        for path in [
            f"/index-jobs/{data['job_id']}",
            f"/documents/{data['document_id']}",
            f"/knowledge-bases/{kb_id}",
        ]:
            assert (await client.get("/api/v1" + path)).status_code == 404
    finally:
        app.dependency_overrides.clear()


async def test_index_version_build_activation_and_concurrent_switch(runtime):
    container, client, _ = runtime
    kb_id, data = await upload(runtime)
    path = f"/api/v1/knowledge-bases/{kb_id}"
    # 构建快照不能漏掉尚在索引的文档。
    assert (await client.post(f"{path}/index-versions")).status_code == 409
    async with container.session_factory() as session:
        assert (
            await run_index_job(session, data["job_id"], container.settings, container=container)
            == "succeeded"
        )
    original = (await client.get(f"{path}/active-index-version")).json()["data"][
        "active_index_version"
    ]
    build = await client.post(f"{path}/index-versions")
    assert build.status_code == 202, build.text
    version = build.json()["data"]["version"]
    assert (await client.post(f"{path}/index-versions")).status_code == 409
    assert (
        await client.post(f"{path}/documents", json={"title": "late", "content": "late"})
    ).status_code == 409
    assert (await client.get(f"{path}/active-index-version")).json()["data"][
        "active_index_version"
    ] == original
    async with container.session_factory() as session:
        assert (
            await run_index_job(
                session, build.json()["data"]["job_id"], container.settings, container=container
            )
            == "succeeded"
        )
    versions = (await client.get(f"{path}/index-versions")).json()["data"]
    ready = next(item for item in versions if item["version"] == version)
    assert ready["status"] == "ready" and ready["document_count"] == 1 and ready["chunk_count"] > 0
    assert (
        await client.put(f"{path}/active-index-version", json={"version": version})
    ).status_code == 200
    # 两个 API 请求各自拥有会话；切换后只能有一个 active，且与知识库指针一致。
    responses = await asyncio.gather(
        *[
            client.put(f"{path}/active-index-version", json={"version": value})
            for value in (original, version)
        ]
    )
    assert all(response.status_code == 200 for response in responses)
    active = (await client.get(f"{path}/active-index-version")).json()["data"][
        "active_index_version"
    ]
    versions = (await client.get(f"{path}/index-versions")).json()["data"]
    assert [item["version"] for item in versions if item["status"] == "active"] == [active]
    response = await client.post(
        "/api/v1/rag/retrieve",
        json={
            "kb_id": kb_id,
            "user_id": "test",
            "query": "退货规则",
            "profile": "custom",
            "retrieval_options": {"mode": "hybrid"},
            "rerank_options": {"enabled": False},
            "query_options": {"enabled": False},
        },
    )
    assert response.status_code == 200 and response.json()["data"]["retrieved_chunks"]
    for item in response.json()["data"]["retrieved_chunks"]:
        assert item["index_version"] == active
    archived = next(item["version"] for item in versions if item["status"] == "archived")
    assert (await client.delete(f"{path}/index-versions/{archived}")).status_code == 204


async def test_artifact_cleanup_preserves_leases_and_filters_unpublished_chunks(runtime):
    from rag_center.modules.indexing.services.artifacts import cleanup_index_artifacts

    container, client, _ = runtime
    # 单独索引避免清理流程接触其他集成场景的数据。
    container.settings.elasticsearch_index = f"rag-artifacts-{uuid4()}"
    kb_id, data = await upload(runtime)
    es = container.external_clients.elasticsearch
    url = f"{container.settings.elasticsearch_url}/{container.settings.elasticsearch_index}"
    try:
        async with container.session_factory() as session:
            assert (
                await run_index_job(
                    session, data["job_id"], container.settings, container=container
                )
                == "succeeded"
            )
            published_id = await session.scalar(
                select(Chunk.id).where(Chunk.document_id == data["document_id"])
            )
        reindex = await client.post(f"/api/v1/documents/{data['document_id']}/index-jobs")
        async with container.session_factory() as session:
            claim = await claim_index_job(
                session, reindex.json()["data"]["job_id"], container.settings
            )
            generation = f"{claim.job.id}:{claim.job.attempt}"
        source = (await es.get(f"{url}/_doc/{published_id}")).json()["_source"]
        source["created_at"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        orphan_id, running_id = str(uuid4()), str(uuid4())
        for chunk_id, gen in [
            (published_id, source["metadata"]["generation"]),
            (orphan_id, f"{uuid4()}:1"),
            (running_id, generation),
        ]:
            response = await es.put(
                f"{url}/_doc/{chunk_id}",
                params={"refresh": "true"},
                json={
                    **source,
                    "chunk_id": chunk_id,
                    "metadata": {**source["metadata"], "generation": gen},
                },
            )
            response.raise_for_status()
        response = await client.post(
            "/api/v1/rag/retrieve",
            json={
                "kb_id": kb_id,
                "user_id": "test",
                "query": "退货规则",
                "profile": "custom",
                "retrieval_options": {"mode": "bm25"},
                "rerank_options": {"enabled": False},
                "query_options": {"enabled": False},
            },
        )
        assert response.status_code == 200, response.text
        assert {item["chunk_id"] for item in response.json()["data"]["retrieved_chunks"]} == {
            published_id
        }
        await cleanup_index_artifacts(container)
        await es.post(f"{url}/_refresh")
        assert (await es.get(f"{url}/_doc/{published_id}")).status_code == 200
        assert (await es.get(f"{url}/_doc/{running_id}")).status_code == 200
        assert (await es.get(f"{url}/_doc/{orphan_id}")).status_code == 404
        async with container.session_factory() as session:
            await session.execute(
                update(IndexJob)
                .where(IndexJob.id == claim.job.id)
                .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
            await session.commit()
        await cleanup_index_artifacts(container)
        await es.post(f"{url}/_refresh")
        assert (await es.get(f"{url}/_doc/{running_id}")).status_code == 404
        assert (await es.get(f"{url}/_doc/{published_id}")).status_code == 200
    finally:
        await es.delete(url)


async def test_claim_is_atomic_under_duplicate_delivery(runtime):
    container, _, _ = runtime
    _, data = await upload(runtime)
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def runner(session, job):
        calls.append(job.id)
        started.set()
        await release.wait()

    async def execute():
        async with container.session_factory() as session:
            return await execute_index_job(
                session, job_id=data["job_id"], runner=runner, settings=container.settings
            )

    first = asyncio.create_task(execute())
    await started.wait()
    assert await execute() == "running"
    release.set()
    assert await first == "succeeded"
    assert calls == [data["job_id"]]


async def test_expired_worker_cannot_replace_new_generation(runtime):
    container, _, _ = runtime
    _, data = await upload(runtime, content="__slow__ 退货规则")

    async def run():
        async with container.session_factory() as session:
            return await run_index_job(
                session, data["job_id"], container.settings, container=container
            )

    old_worker = asyncio.create_task(run())
    try:
        async with asyncio.timeout(10):
            while True:
                async with container.session_factory() as session:
                    job = await session.get(IndexJob, data["job_id"])
                    if job.status == "running":
                        break
                await asyncio.sleep(0.01)
        assert not old_worker.done()
        # 首个执行器仍在准备产物时回收租约，第二个执行器领取新代次。
        async with container.session_factory() as session:
            await session.execute(
                update(IndexJob)
                .where(IndexJob.id == data["job_id"])
                .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
            await session.commit()
            assert await recover_expired_index_jobs(session) >= 1
        assert await run() == "succeeded"
        assert await old_worker == "running"
        async with container.session_factory() as session:
            job = await session.get(IndexJob, data["job_id"])
            chunks = list(
                (
                    await session.scalars(
                        select(Chunk).where(Chunk.document_id == data["document_id"])
                    )
                ).all()
            )
            assert job.status == "succeeded" and job.attempt == 2 and chunks
            assert {chunk.metadata_["generation"] for chunk in chunks} == {f"{job.id}:2"}
    finally:
        if not old_worker.done():
            old_worker.cancel()
        await asyncio.gather(old_worker, return_exceptions=True)


async def test_retry_backoff_outbox_recovery_and_exhaustion(runtime):
    container, _, _ = runtime
    _, data = await upload(runtime)
    async with container.session_factory() as session:
        original = await claim_index_job(session, data["job_id"], container.settings)
        assert original is not None
        await fail_index_job(
            session,
            job_id=data["job_id"],
            lease_token=original.lease_token,
            error_message="temporary",
            retry_backoff_seconds=1,
        )
        assert await claim_index_job(session, data["job_id"], container.settings) is None
        await session.execute(
            update(IndexJob)
            .where(IndexJob.id == data["job_id"])
            .values(available_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()
        second = await claim_index_job(session, data["job_id"], container.settings)
        assert second is not None
        await session.execute(
            update(IndexJob)
            .where(IndexJob.id == data["job_id"])
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()
        assert await recover_expired_index_jobs(session) >= 1
        with pytest.raises(IndexJobLeaseLost):
            await lock_valid_lease(session, second.job)
        await session.rollback()
        third = await claim_index_job(session, data["job_id"], container.settings)
        assert third is not None and third.job.attempt == 3
        await session.execute(
            update(IndexJob)
            .where(IndexJob.id == data["job_id"])
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()
        await recover_expired_index_jobs(session)
        job = await session.get(IndexJob, data["job_id"], populate_existing=True)
        assert job.status == "failed"
        document = await session.get(Document, data["document_id"])
        assert document.status == 2


async def test_broker_failure_preserves_outbox_and_redis_accepts_redelivery(runtime):
    container, _, _ = runtime
    _, data = await upload(runtime)
    failed_broker = Celery("test", broker="redis://127.0.0.1:1/0")
    failed_broker.conf.broker_transport_options = {
        "socket_connect_timeout": 0.1,
        "socket_timeout": 0.1,
        "max_retries": 0,
    }
    broker = Celery("test", broker=container.settings.redis_url)

    def publish(app):
        return lambda topic, payload: app.send_task(
            "rag_center.index_document", kwargs=payload, retry=False, queue="rag-integration"
        )

    async with container.session_factory() as session:
        assert await publish_pending_index_outbox(session, publisher=publish(failed_broker)) == 0
        assert await publish_pending_index_outbox(session, publisher=publish(broker)) >= 1
        await session.execute(
            update(IndexOutbox)
            .where(IndexOutbox.job_id == data["job_id"])
            .values(published_at=datetime.now(UTC) - timedelta(minutes=2))
        )
        await session.commit()
        assert await repair_queued_deliveries(session, redispatch_seconds=1) >= 1
        assert await publish_pending_index_outbox(session, publisher=publish(broker)) >= 1
    assert await container.redis.llen("rag-integration") >= 2
    await container.redis.delete("rag-integration")
    failed_broker.close()
    broker.close()


async def test_scheduler_publisher_keeps_explicit_app_in_thread_pool(runtime, monkeypatch):
    import base64
    import json

    from rag_center import celery_app as module

    container, _, _ = runtime
    _, data = await upload(runtime)
    queue = f"publisher-test-{uuid4()}"
    broker = Celery("explicit-publisher", broker=container.settings.redis_url, set_as_current=False)
    broker.conf.task_default_queue = queue
    monkeypatch.setattr(module, "celery_app", broker)
    try:
        async with container.session_factory() as session:
            assert (
                await publish_pending_index_outbox(session, publisher=module.publish_index_job) >= 1
            )
        messages = [json.loads(raw) for raw in await container.redis.lrange(queue, 0, -1)]
        assert all(
            message["headers"]["task"] == "rag_center.index_document" for message in messages
        )
        bodies = [json.loads(base64.b64decode(message["body"])) for message in messages]
        assert any(
            args == [] and kwargs == {"job_id": data["job_id"]} for args, kwargs, _ in bodies
        )
    finally:
        await container.redis.delete(queue)
        broker.close()


async def test_permanent_parse_failure_does_not_publish_artifacts(runtime):
    container, _, _ = runtime
    _, data = await upload(runtime)
    async with container.session_factory() as session:
        await session.execute(
            update(Document)
            .where(Document.id == data["document_id"])
            .values(content="", source_file_path="missing-file.pdf")
        )
        await session.commit()
        assert (
            await run_index_job(session, data["job_id"], container.settings, container=container)
            == "failed"
        )
        assert (await session.get(IndexJob, data["job_id"], populate_existing=True)).attempt == 1
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Chunk)
                .where(Chunk.document_id == data["document_id"])
            )
            == 0
        )


async def test_disabled_elasticsearch_indexes_and_degrades_hybrid_without_es(runtime):
    container, client, _ = runtime
    container.settings.elasticsearch_enabled = False
    container.settings.elasticsearch_url = "http://127.0.0.1:1"
    kb_id, data = await upload(runtime)
    async with container.session_factory() as session:
        assert (
            await run_index_job(session, data["job_id"], container.settings, container=container)
            == "succeeded"
        )
    response = await client.post(
        "/api/v1/rag/retrieve",
        json={
            "kb_id": kb_id,
            "user_id": "test",
            "query": "退货规则",
            "profile": "custom",
            "retrieval_options": {"mode": "hybrid"},
            "rerank_options": {"enabled": False},
            "query_options": {"enabled": False},
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["retrieved_chunks"]
    assert result["metadata"]["retrieval"]["degraded"] is True
    assert {item["retrieval_source"] for item in result["retrieved_chunks"]} == {"vector"}


async def test_heartbeat_uses_distinct_sessions(runtime):
    container, _, _ = runtime
    _, data = await upload(runtime)
    settings = container.settings.model_copy(update={"index_job_lease_seconds": 1})

    async def runner(session, job):
        await asyncio.sleep(1.2)
        await lock_valid_lease(session, job)

    async with container.session_factory() as session:
        assert (
            await execute_index_job(
                session,
                job_id=data["job_id"],
                runner=runner,
                settings=settings,
                session_factory=container.session_factory,
            )
            == "succeeded"
        )


async def test_schema_and_async_sessions_are_isolated(runtime):
    container, client, _ = runtime
    async with container.session_factory() as first, container.session_factory() as second:
        assert await first.scalar(text("SELECT pg_backend_pid()")) != await second.scalar(
            text("SELECT pg_backend_pid()")
        )
        assert await first.scalar(
            text("SELECT extversion FROM pg_extension WHERE extname='vector'")
        )
    response = await client.get("/health/ready")
    assert response.status_code == 200, response.text


async def test_legacy_resources_are_recovered_once(runtime):
    container, _, tenant_id = runtime
    kb_id, _ = await upload(runtime)
    from rag_center.modules.indexing.services.legacy_recovery import recover_legacy_index_resources

    async with container.session_factory() as session:
        legacy_id = str(uuid4())
        session.add(
            Document(
                id=legacy_id,
                kb_id=kb_id,
                tenant_id=tenant_id,
                title="legacy",
                content="legacy text",
                source_type="text",
                status=3,
            )
        )
        await session.commit()
        assert await recover_legacy_index_resources(session, container.settings) == 1
        assert await recover_legacy_index_resources(session, container.settings) == 0
        job = await session.scalar(select(IndexJob).where(IndexJob.resource_id == legacy_id))
        assert job.status == "queued"
        assert await session.scalar(select(IndexOutbox.id).where(IndexOutbox.job_id == job.id))


async def test_research_parallel_branches_own_separate_sessions(runtime):
    container, _, _ = runtime
    from rag_center.modules.retrieval.services.rag_service import RagService

    release = asyncio.Event()
    sessions = []
    async with container.session_factory() as parent:
        service = RagService(parent, http_clients=container.external_clients)

        async def branch():
            async with service.retrieval_branch() as once:
                sessions.append(once.backend.db)
                if len(sessions) == 2:
                    release.set()
                await release.wait()
                return await once.backend.db.scalar(text("SELECT pg_backend_pid()"))

        pids = await asyncio.gather(branch(), branch())
        assert sessions[0] is not sessions[1] and parent not in sessions
        assert len(set(pids)) == 2


async def test_prefork_worker_crash_recovers_with_new_lease(runtime, tmp_path):
    """真实 prefork 子进程中断后恢复，第二个任务继续复用同一子进程资源。"""
    import os
    import signal
    import subprocess
    import sys

    from rag_center.modules.indexing.services.job_executor import recover_expired_index_jobs

    container, client, _ = runtime
    _, data = await upload(runtime, content="__slow__ worker interruption")
    queue = f"worker-test-{uuid4()}"
    environment = {key: value for key, value in os.environ.items() if not key.startswith("RAG_")}
    environment.update(
        {
            "RAG_DATABASE_URL": container.settings.database_url,
            "RAG_REDIS_URL": container.settings.redis_url,
            "RAG_CELERY_BROKER_URL": container.settings.redis_url,
            "RAG_CELERY_RESULT_BACKEND": container.settings.redis_url,
            "RAG_MODEL_BASE_URL": container.settings.model_base_url,
            "RAG_MODEL_API_KEY": "protocol-stub",
            "RAG_ELASTICSEARCH_ENABLED": "false",
            "RAG_INDEX_JOB_LEASE_SECONDS": "1",
            "RAG_DOCUMENT_STORAGE_PATH": container.settings.document_storage_path,
            "RAG_LOG_DIR": str(tmp_path / "worker-logs"),
        }
    )
    broker = Celery("worker-test", broker=container.settings.redis_url)
    processes = []

    def start_worker(log):
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "rag_center.cli",
                "worker",
                "run",
                "--concurrency",
                "1",
                "--queue",
                queue,
            ],
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        processes.append(process)
        return process

    async def status(job_id):
        async with container.session_factory() as session:
            job = await session.get(IndexJob, job_id)
            return job.status

    async def until(job_id, expected):
        async with asyncio.timeout(30):
            while await status(job_id) != expected:
                if await status(job_id) == "failed":
                    pytest.fail("Worker 任务进入失败终态")
                await asyncio.sleep(0.1)

    with (tmp_path / "worker.log").open("w") as log:
        try:
            first = start_worker(log)
            await asyncio.to_thread(
                broker.send_task,
                "rag_center.index_document",
                kwargs={"job_id": data["job_id"]},
                queue=queue,
            )
            await until(data["job_id"], "running")
            os.killpg(first.pid, signal.SIGKILL)
            await asyncio.to_thread(first.wait, timeout=10)
            await asyncio.sleep(1.2)
            async with container.session_factory() as session:
                await recover_expired_index_jobs(session)
            start_worker(log)
            await asyncio.to_thread(
                broker.send_task,
                "rag_center.index_document",
                kwargs={"job_id": data["job_id"]},
                queue=queue,
            )
            await until(data["job_id"], "succeeded")
            # 连续第二个任务必须成功，覆盖同一事件循环复用连接池。
            reindex = await client.post(f"/api/v1/documents/{data['document_id']}/index-jobs")
            assert reindex.status_code == 202
            second_id = reindex.json()["data"]["job_id"]
            await asyncio.to_thread(
                broker.send_task,
                "rag_center.index_document",
                kwargs={"job_id": second_id},
                queue=queue,
            )
            await until(second_id, "succeeded")
        finally:
            for process in processes:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        await asyncio.to_thread(process.wait, timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        await asyncio.to_thread(process.wait, timeout=10)
            broker.close()
            await container.redis.delete(queue)
