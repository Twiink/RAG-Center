from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from rag_center.core.logging import get_logger
from rag_center.modules.indexing.models.index_job import IndexJob, IndexOutbox
from rag_center.modules.indexing.services.job_executor import enqueue_delivery

logger = get_logger(__name__)


class MessagePublisher(Protocol):
    def __call__(self, topic: str, payload: dict) -> object:
        """同步发布；仅在 broker 接受消息后返回。"""


async def publish_pending_index_outbox(
    session: AsyncSession, *, publisher: MessagePublisher, batch_size: int = 100
) -> int:
    """SKIP LOCKED 分配投递，broker 发布成功后才提交标记；崩溃允许重复投递。"""
    rows = (
        await session.scalars(
            select(IndexOutbox)
            .join(IndexJob)
            .where(
                IndexOutbox.published_at.is_(None),
                IndexJob.status == "queued",
                IndexJob.available_at <= datetime.now(UTC),
            )
            .order_by(IndexOutbox.created_at, IndexOutbox.id)
            .limit(batch_size)
            .with_for_update(of=IndexOutbox, skip_locked=True)
        )
    ).all()
    published = 0
    for row in rows:
        row.attempts += 1
        try:
            # Celery broker I/O 在线程池执行，不能阻塞调度事件循环。
            result = await asyncio.to_thread(publisher, row.topic, row.payload)
            if getattr(result, "id", None) is None:
                raise RuntimeError("broker 未确认发布")
            row.published_at = datetime.now(UTC)
            row.last_error = None
            published += 1
        except Exception as exc:
            row.last_error = type(exc).__name__
            logger.warning(
                "INDEX_OUTBOX_PUBLISH_RETRY | job_id=%s | error=%s", row.job_id, type(exc).__name__
            )
    await session.commit()
    return published


async def repair_queued_deliveries(session: AsyncSession, *, redispatch_seconds: int = 60) -> int:
    """补建丢失投递；queued 超时且没有待发消息时再发，claim 吸收重复消息。"""
    last_published = (
        select(func.max(IndexOutbox.published_at))
        .where(IndexOutbox.job_id == IndexJob.id)
        .correlate(IndexJob)
        .scalar_subquery()
    )
    pending = exists(
        select(IndexOutbox.id).where(
            IndexOutbox.job_id == IndexJob.id, IndexOutbox.published_at.is_(None)
        )
    )
    now = datetime.now(UTC)
    jobs = (
        await session.scalars(
            select(IndexJob)
            .where(
                IndexJob.status == "queued",
                IndexJob.available_at <= now,
                ~pending,
                func.coalesce(last_published, IndexJob.created_at)
                < now - timedelta(seconds=redispatch_seconds),
            )
            .limit(100)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for job in jobs:
        enqueue_delivery(session, job)
    await session.commit()
    return len(jobs)


async def repair_outbox_for_job(session: AsyncSession, job_id: str) -> None:
    job = await session.scalar(select(IndexJob).where(IndexJob.id == job_id).with_for_update())
    if job is not None and job.status == "queued":
        pending = await session.scalar(
            select(IndexOutbox.id)
            .where(IndexOutbox.job_id == job_id, IndexOutbox.published_at.is_(None))
            .limit(1)
        )
        if pending is None:
            enqueue_delivery(session, job)
    await session.commit()
