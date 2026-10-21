from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from rag_center.core.config import Settings
from rag_center.core.exceptions import AppError
from rag_center.core.logging import get_logger
from rag_center.infrastructure.ids import generate_uuid
from rag_center.modules.indexing.models.index_job import IndexJob, IndexOutbox
from rag_center.modules.indexing.services.jobs import IndexJobStatus

logger = get_logger(__name__)


class IndexJobRunner(Protocol):
    async def __call__(self, session: AsyncSession, /, job: IndexJob) -> None:
        """准备并发布当前代次；发布事务由执行器提交。"""


class PermanentIndexJobError(Exception):
    """任务不可重试的错误。"""


class IndexJobLeaseLost(Exception):
    """租约已失效，当前执行器必须放弃所有发布操作。"""


@dataclass(slots=True)
class ClaimResult:
    job: IndexJob
    lease_token: str
    expires_at: datetime


def _now() -> datetime:
    return datetime.now(UTC)


def enqueue_delivery(session: AsyncSession, job: IndexJob) -> None:
    """在任务状态变更的同一事务中创建下一次投递。"""
    session.add(
        IndexOutbox(
            id=generate_uuid(),
            job_id=job.id,
            topic=f"index.{job.job_type}",
            payload={"job_id": job.id},
        )
    )


async def claim_index_job(
    session: AsyncSession, job_id: str, settings: Settings
) -> ClaimResult | None:
    """短事务抢占；未到重试时间、已耗尽或有效租约中的任务不能再执行。"""
    job = await session.scalar(
        select(IndexJob).where(IndexJob.id == job_id).with_for_update(skip_locked=True)
    )
    if job is None or job.status not in {IndexJobStatus.QUEUED, IndexJobStatus.RUNNING}:
        await session.rollback()
        return None
    now = _now()
    if (
        job.status == IndexJobStatus.RUNNING and job.lease_expires_at and job.lease_expires_at > now
    ) or (job.status == IndexJobStatus.QUEUED and job.available_at and job.available_at > now):
        await session.rollback()
        return None
    if job.attempt >= job.max_attempts:
        job.status = IndexJobStatus.FAILED
        job.lease_token = None
        job.lease_expires_at = None
        await mark_resource_failed(session, job)
        await session.commit()
        return None
    token = generate_uuid()
    expires_at = now + timedelta(seconds=settings.index_job_lease_seconds)
    job.status = IndexJobStatus.RUNNING
    job.lease_token = token
    job.lease_expires_at = expires_at
    job.attempt += 1
    await session.commit()
    return ClaimResult(job=job, lease_token=token, expires_at=expires_at)


async def renew_index_job_lease(
    session: AsyncSession, *, job_id: str, lease_token: str, settings: Settings
) -> bool:
    """仅续期仍有效的租约，禁止复活已经过期的 Worker。"""
    result = await session.execute(
        update(IndexJob)
        .where(
            IndexJob.id == job_id,
            IndexJob.lease_token == lease_token,
            IndexJob.status == IndexJobStatus.RUNNING,
            IndexJob.lease_expires_at > _now(),
        )
        .values(lease_expires_at=_now() + timedelta(seconds=settings.index_job_lease_seconds))
    )
    await session.commit()
    return bool(getattr(result, "rowcount", 0))


async def lock_valid_lease(session: AsyncSession, job: IndexJob) -> None:
    """发布前锁定任务行；锁持续到产物与成功终态一并提交。"""
    valid = await session.scalar(
        select(IndexJob.id)
        .where(
            IndexJob.id == job.id,
            IndexJob.lease_token == job.lease_token,
            IndexJob.status == IndexJobStatus.RUNNING,
            IndexJob.lease_expires_at > _now(),
        )
        .with_for_update()
    )
    if valid is None:
        raise IndexJobLeaseLost(job.id)


async def complete_index_job(session: AsyncSession, *, job_id: str, lease_token: str) -> bool:
    result = await session.execute(
        update(IndexJob)
        .where(
            IndexJob.id == job_id,
            IndexJob.lease_token == lease_token,
            IndexJob.status == IndexJobStatus.RUNNING,
            IndexJob.lease_expires_at > _now(),
        )
        .values(
            status=IndexJobStatus.SUCCEEDED,
            lease_token=None,
            lease_expires_at=None,
            error_message=None,
        )
    )
    if not getattr(result, "rowcount", 0):
        await session.rollback()
        return False
    await session.commit()
    return True


async def mark_resource_failed(session: AsyncSession, job: IndexJob) -> None:
    """资源状态和任务失败终态在同一事务中更新。"""
    from rag_center.modules.documents.models.document import Document
    from rag_center.modules.documents.status import DocumentStatus
    from rag_center.modules.indexing.models.index_version import KnowledgeBaseIndexVersion

    if job.job_type == "document":
        await session.execute(
            update(Document)
            .where(Document.id == job.resource_id, Document.tenant_id == job.tenant_id)
            .values(status=DocumentStatus.FAILED, error_message=job.error_message)
        )
    elif job.job_type == "index_version":
        await session.execute(
            update(KnowledgeBaseIndexVersion)
            .where(
                KnowledgeBaseIndexVersion.kb_id == job.resource_id,
                KnowledgeBaseIndexVersion.tenant_id == job.tenant_id,
                KnowledgeBaseIndexVersion.version == job.payload.get("version"),
                KnowledgeBaseIndexVersion.status == "building",
            )
            .values(status="failed", error_message=job.error_message, completed_at=_now())
        )


async def fail_index_job(
    session: AsyncSession,
    *,
    job_id: str,
    lease_token: str,
    error_message: str,
    retry_backoff_seconds: int,
    permanent: bool = False,
) -> bool:
    """失败和下一次 outbox 原子提交；永久错误直接终止。"""
    job = await session.scalar(select(IndexJob).where(IndexJob.id == job_id).with_for_update())
    if job is None or job.lease_token != lease_token or job.status != IndexJobStatus.RUNNING:
        await session.rollback()
        return False
    if job.lease_expires_at and job.lease_expires_at <= _now():
        await session.rollback()
        return False
    job.error_message = error_message[:2000]
    job.lease_token = None
    job.lease_expires_at = None
    if not permanent and job.attempt < job.max_attempts:
        job.status = IndexJobStatus.QUEUED
        job.available_at = _now() + timedelta(
            seconds=retry_backoff_seconds * 2 ** max(0, job.attempt - 1)
        )
        enqueue_delivery(session, job)
    else:
        job.status = IndexJobStatus.FAILED
        await mark_resource_failed(session, job)
    await session.commit()
    return True


async def recover_expired_index_jobs(session: AsyncSession) -> int:
    """回收过期租约并补发；达到总尝试上限时写入资源及任务失败终态。"""
    jobs = (
        await session.scalars(
            select(IndexJob)
            .where(
                IndexJob.status == IndexJobStatus.RUNNING,
                IndexJob.lease_expires_at <= _now(),
            )
            .limit(100)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for job in jobs:
        job.lease_token = None
        job.lease_expires_at = None
        job.error_message = "Worker 租约过期"
        if job.attempt >= job.max_attempts:
            job.status = IndexJobStatus.FAILED
            await mark_resource_failed(session, job)
        else:
            job.status = IndexJobStatus.QUEUED
            job.available_at = _now()
            enqueue_delivery(session, job)
    await session.commit()
    return len(jobs)


async def _heartbeat(
    session_factory: Callable, job_id: str, token: str, settings: Settings
) -> None:
    while True:
        await asyncio.sleep(settings.index_job_lease_seconds / 3)
        async with session_factory() as session:
            if not await renew_index_job_lease(
                session, job_id=job_id, lease_token=token, settings=settings
            ):
                raise IndexJobLeaseLost(job_id)


async def execute_index_job(
    session: AsyncSession,
    *,
    job_id: str,
    runner: IndexJobRunner,
    settings: Settings,
    session_factory: Callable | None = None,
) -> IndexJobStatus:
    """独立会话续租，发布产物与任务成功同事务提交；失效执行器只能回滚。"""
    claim = await claim_index_job(session, job_id, settings)
    if claim is None:
        current = await session.scalar(select(IndexJob).where(IndexJob.id == job_id))
        if current is None:
            raise ValueError(f"index job not found: {job_id}")
        return IndexJobStatus(current.status)
    job = claim.job
    # rollback 会过期 ORM 属性；后续错误路径仅使用抢占时的稳定快照。
    job_snapshot = IndexJob(
        id=job.id,
        tenant_id=job.tenant_id,
        resource_id=job.resource_id,
        job_type=job.job_type,
        payload=dict(job.payload),
        attempt=job.attempt,
        max_attempts=job.max_attempts,
        status=job.status,
        lease_token=claim.lease_token,
    )
    heartbeat = (
        asyncio.create_task(_heartbeat(session_factory, job_id, claim.lease_token, settings))
        if session_factory
        else None
    )
    work = asyncio.create_task(runner(session, job_snapshot))
    try:
        if heartbeat:
            done, _ = await asyncio.wait({work, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
            if heartbeat in done:
                await heartbeat
        await work
        completed = await complete_index_job(session, job_id=job_id, lease_token=claim.lease_token)
        return IndexJobStatus.SUCCEEDED if completed else IndexJobStatus.RUNNING
    except IndexJobLeaseLost:
        work.cancel()
        # runner 自己也可能因失效租约结束；回收结果时不能再次抛出其原异常。
        with suppress(asyncio.CancelledError, Exception):
            await work
        await session.rollback()
        return IndexJobStatus.RUNNING
    except Exception as exc:
        await session.rollback()
        permanent = isinstance(exc, PermanentIndexJobError)
        error_name = exc.error_name if isinstance(exc, AppError) else type(exc).__name__
        logger.warning(
            "INDEX_JOB_ATTEMPT_FAILED | job_id=%s | attempt=%s | max_attempts=%s | error_type=%s",
            job_id,
            job_snapshot.attempt,
            job_snapshot.max_attempts,
            error_name,
        )
        updated = await fail_index_job(
            session,
            job_id=job_id,
            lease_token=claim.lease_token,
            error_message=str(exc) if permanent else error_name,
            retry_backoff_seconds=settings.index_job_retry_backoff_seconds,
            permanent=permanent,
        )
        if not updated:
            return IndexJobStatus.RUNNING
        return (
            IndexJobStatus.FAILED
            if permanent or job_snapshot.attempt >= job_snapshot.max_attempts
            else IndexJobStatus.QUEUED
        )
    finally:
        work.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await work
        if heartbeat:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await heartbeat
