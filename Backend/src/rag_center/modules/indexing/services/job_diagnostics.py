from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from rag_center.modules.indexing.models.index_job import IndexJob, IndexOutbox


class JobDiagnosticsService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_status_stats(self) -> dict[str, int]:
        """获取任务状态分布统计"""
        result = await self.session.execute(
            select(IndexJob.status, func.count(IndexJob.id)).group_by(IndexJob.status)
        )
        return {status: count for status, count in result}

    async def get_recent_failures(self, limit: int = 20) -> list[dict[str, Any]]:
        """获取最近失败的任务及错误类型统计"""
        result = await self.session.execute(
            select(IndexJob)
            .where(IndexJob.status == "failed")
            .order_by(IndexJob.updated_at.desc())
            .limit(limit)
        )
        jobs = result.scalars().all()

        failures = []
        for job in jobs:
            error_code = getattr(job, "error_code", None)
            failures.append(
                {
                    "job_id": job.id,
                    "resource_id": job.resource_id,
                    "job_type": job.job_type,
                    "error_code": error_code,
                    "error_message": job.error_message[:200] if job.error_message else None,
                    "updated_at": job.updated_at.isoformat(),
                }
            )

        return failures

    async def get_error_type_counts(self) -> dict[str, int]:
        """按错误类型统计失败任务数量"""
        result = await self.session.execute(select(IndexJob).where(IndexJob.status == "failed"))
        jobs = result.scalars().all()

        counts: dict[str, int] = {}
        for job in jobs:
            error_code = getattr(job, "error_code", "UNKNOWN")
            if error_code:
                counts[error_code] = counts.get(error_code, 0) + 1

        return counts

    async def get_expired_leases(self) -> list[dict[str, Any]]:
        """获取过期租约的运行中任务"""
        now = datetime.now(UTC)
        result = await self.session.execute(
            select(IndexJob)
            .where(IndexJob.status == "running", IndexJob.lease_expires_at < now)
            .order_by(IndexJob.lease_expires_at)
        )
        jobs = result.scalars().all()

        return [
            {
                "job_id": job.id,
                "resource_id": job.resource_id,
                "lease_token": job.lease_token,
                "lease_expires_at": job.lease_expires_at.isoformat()
                if job.lease_expires_at
                else None,
                "expired_seconds": (
                    int((now - job.lease_expires_at).total_seconds()) if job.lease_expires_at else 0
                ),
            }
            for job in jobs
        ]

    async def get_outbox_lag(self) -> dict[str, Any]:
        """获取未发布消息的统计信息"""
        result = await self.session.execute(
            select(
                func.count(IndexOutbox.id).label("unpublished_count"),
                func.min(IndexOutbox.created_at).label("oldest_created_at"),
            ).where(IndexOutbox.published_at.is_(None))
        )
        row = result.one()

        oldest_age_seconds = None
        if row.oldest_created_at:
            oldest_age_seconds = int((datetime.now(UTC) - row.oldest_created_at).total_seconds())

        return {
            "unpublished_count": row.unpublished_count or 0,
            "oldest_age_seconds": oldest_age_seconds,
        }

    async def retry_job(self, job_id: str) -> None:
        """重试失败的任务"""
        result = await self.session.execute(select(IndexJob).where(IndexJob.id == job_id))
        job = result.scalar_one_or_none()

        if job is None:
            raise ValueError(f"任务不存在: {job_id}")

        if job.status != "failed":
            raise ValueError(f"只能重试失败任务，当前状态: {job.status}")

        job.status = "queued"
        job.error_message = None

        from rag_center.modules.indexing.services.job_executor import enqueue_delivery

        enqueue_delivery(self.session, job)

        await self.session.commit()
