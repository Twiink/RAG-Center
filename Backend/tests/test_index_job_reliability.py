from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from rag_center.core.config import Settings
from rag_center.modules.indexing.models.index_job import IndexJob
from rag_center.modules.indexing.services.job_executor import (
    PermanentIndexJobError,
    claim_index_job,
    complete_index_job,
    execute_index_job,
    fail_index_job,
    recover_expired_index_jobs,
    renew_index_job_lease,
)
from rag_center.modules.indexing.services.jobs import IndexJobStatus


def make_settings() -> Settings:
    return Settings(
        database_url="postgresql+psycopg://rag:rag@localhost/rag_test",
        redis_url="redis://localhost:6379/0",
        celery_broker_url="redis://localhost:6379/0",
        celery_result_backend="redis://localhost:6379/1",
        embedding_api_key="test-key",
        llm_api_key="test-key",
    )


def make_job(**overrides) -> IndexJob:
    values = {
        "id": "job-1",
        "tenant_id": "tenant-1",
        "resource_id": "resource-1",
        "job_type": "document",
        "payload": {"document_id": "resource-1"},
        "status": IndexJobStatus.QUEUED,
        "attempt": 0,
        "max_attempts": 3,
    }
    values.update(overrides)
    return IndexJob(**values)


def mock_session() -> MagicMock:
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.execute = AsyncMock()
    return session


class UpdateResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


@pytest.mark.asyncio
async def test_claim_updates_lease_attempt_and_commits() -> None:
    job = make_job()
    session = mock_session()
    session.scalar = AsyncMock(return_value=job)

    claim = await claim_index_job(session, "job-1", make_settings())

    assert claim is not None
    assert job.status == IndexJobStatus.RUNNING
    assert job.attempt == 1
    assert job.lease_token == claim.lease_token
    assert job.lease_expires_at == claim.expires_at
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_claim_reuses_running_job_only_after_lease_expiry() -> None:
    active = make_job(
        status=IndexJobStatus.RUNNING,
        attempt=1,
        lease_token="old",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    expired = make_job(
        status=IndexJobStatus.RUNNING,
        attempt=2,
        lease_token="old",
        lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    session = mock_session()
    session.scalar = AsyncMock(side_effect=[active, expired])

    assert await claim_index_job(session, "job-1", make_settings()) is None
    claim = await claim_index_job(session, "job-1", make_settings())
    assert claim is not None
    assert expired.attempt == 3


@pytest.mark.asyncio
async def test_lease_renewal_and_completion_require_matching_token() -> None:
    session = mock_session()
    session.execute = AsyncMock(side_effect=[UpdateResult(0), UpdateResult(1), UpdateResult(1)])

    assert (
        await renew_index_job_lease(
            session, job_id="job-1", lease_token="wrong", settings=make_settings()
        )
        is False
    )
    assert (
        await renew_index_job_lease(
            session, job_id="job-1", lease_token="right", settings=make_settings()
        )
        is True
    )
    assert await complete_index_job(session, job_id="job-1", lease_token="right") is True


@pytest.mark.asyncio
async def test_retry_uses_exponential_backoff_and_exhausts_to_failed() -> None:
    job = make_job(
        status=IndexJobStatus.RUNNING,
        attempt=1,
        max_attempts=3,
        lease_token="token",
    )
    session = mock_session()
    session.scalar = AsyncMock(return_value=job)

    assert await fail_index_job(
        session,
        job_id=job.id,
        lease_token="token",
        error_message="timeout",
        retry_backoff_seconds=5,
    )
    assert job.status == IndexJobStatus.QUEUED
    assert job.available_at is not None
    session.add.assert_called_once()

    job.attempt = 3
    job.lease_token = "token"
    job.status = IndexJobStatus.RUNNING
    assert await fail_index_job(
        session,
        job_id=job.id,
        lease_token="token",
        error_message="timeout",
        retry_backoff_seconds=5,
    )
    assert job.status == IndexJobStatus.FAILED
    assert job.lease_token is None


@pytest.mark.asyncio
async def test_execute_job_is_idempotent_for_duplicate_delivery() -> None:
    job = make_job(status=IndexJobStatus.SUCCEEDED)
    session = mock_session()
    session.scalar = AsyncMock(return_value=job)
    runner = AsyncMock()

    result = await execute_index_job(
        session, job_id=job.id, runner=runner, settings=make_settings()
    )

    assert result == IndexJobStatus.SUCCEEDED
    runner.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_job_permanent_failure_writes_terminal_state() -> None:
    job = make_job(attempt=0)
    session = mock_session()
    session.scalar = AsyncMock(side_effect=[job, job, job])

    async def runner(_, __) -> None:
        raise PermanentIndexJobError("bad payload")

    result = await execute_index_job(
        session, job_id=job.id, runner=runner, settings=make_settings()
    )
    assert result == IndexJobStatus.FAILED
    assert job.status == IndexJobStatus.FAILED


@pytest.mark.asyncio
async def test_execute_job_transient_failure_requeues() -> None:
    job = make_job(max_attempts=3)
    session = mock_session()
    session.scalar = AsyncMock(side_effect=[job, job, job])

    async def runner(_, __) -> None:
        raise TimeoutError("provider timeout")

    result = await execute_index_job(
        session, job_id=job.id, runner=runner, settings=make_settings()
    )
    assert result == IndexJobStatus.QUEUED
    assert job.status == IndexJobStatus.QUEUED


@pytest.mark.asyncio
async def test_recover_expired_running_jobs() -> None:
    session = mock_session()
    jobs = [
        make_job(status="running", attempt=1),
        make_job(id="job-2", status="running", attempt=3),
    ]
    session.scalars = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=jobs)))
    assert await recover_expired_index_jobs(session) == 2
    assert jobs[0].status == "queued"
    assert jobs[1].status == "failed"
    session.add.assert_called_once()


def test_partial_unique_constraint_only_covers_active_statuses() -> None:
    table = IndexJob.__table__
    index = next(index for index in table.indexes if index.name == "uq_index_jobs_active_resource")
    assert index.unique is True
    assert "status" in str(index.dialect_options["postgresql"]["where"])
