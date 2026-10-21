from sqlalchemy.ext.asyncio import AsyncSession

from rag_center.core.exceptions import AppError, ErrorCode
from rag_center.modules.indexing.repositories.job_repository import IndexJobRepository
from rag_center.modules.indexing.schemas.jobs import IndexJobData


class IndexJobService:
    def __init__(self, session: AsyncSession):
        self.repository = IndexJobRepository(session)

    async def get(self, job_id: str, tenant_id: str) -> IndexJobData:
        job = await self.repository.get_for_tenant(job_id, tenant_id)
        if job is None:
            raise AppError(ErrorCode.NOT_FOUND, msg="索引任务不存在")
        return IndexJobData.model_validate(
            {
                "job_id": job.id,
                "resource_id": job.resource_id,
                "job_type": job.job_type,
                "status": job.status,
                "attempt": job.attempt,
                "max_attempts": job.max_attempts,
                "error_message": job.error_message,
                "created_at": job.created_at,
                "updated_at": job.updated_at,
            }
        )
