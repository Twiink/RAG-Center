from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from rag_center.core.exceptions import AppError, ErrorCode
from rag_center.infrastructure.ids import generate_uuid


class IndexJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(slots=True)
class QueueIndexJobResult:
    job_id: str
    resource_id: str
    job_type: str


async def queue_index_job(
    session: AsyncSession,
    *,
    tenant_id: str,
    resource_id: str,
    job_type: str,
    payload: dict | None = None,
    max_attempts: int,
    lease_seconds: int,
) -> QueueIndexJobResult:
    """在同一业务事务内写入任务和 outbox 消息。

    调用方负责 commit；本函数只 flush，保证 API 能拿到 job_id。
    """

    from rag_center.modules.indexing.models.index_job import IndexJob, IndexOutbox

    if await has_active_index_job(session, resource_id):
        raise AppError(ErrorCode.DATA_DUPLICATE, msg="该资源已有活动索引任务")

    job = IndexJob(
        id=generate_uuid(),
        tenant_id=tenant_id,
        resource_id=resource_id,
        job_type=job_type,
        payload=payload or {},
        status=IndexJobStatus.QUEUED,
        attempt=0,
        max_attempts=max_attempts,
        available_at=datetime.now(UTC),
        lease_expires_at=None,
    )
    session.add(job)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        if (
            getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            == "uq_index_jobs_active_resource"
        ):
            raise AppError(ErrorCode.DATA_DUPLICATE, msg="该资源已有活动索引任务") from exc
        raise
    session.add(
        IndexOutbox(
            id=generate_uuid(),
            job_id=job.id,
            topic=f"index.{job_type}",
            payload={"job_id": job.id},
        )
    )
    await session.flush()
    return QueueIndexJobResult(job_id=job.id, resource_id=resource_id, job_type=job_type)


async def has_active_index_job(session: AsyncSession, resource_id: str) -> bool:
    from sqlalchemy import func

    from rag_center.modules.indexing.models.index_job import IndexJob

    stmt = (
        select(func.count())
        .select_from(IndexJob)
        .where(
            IndexJob.resource_id == resource_id,
            IndexJob.status.in_((IndexJobStatus.QUEUED, IndexJobStatus.RUNNING)),
        )
    )
    return int(await session.scalar(stmt) or 0) > 0


def lease_expiry(lease_seconds: int) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=lease_seconds)
