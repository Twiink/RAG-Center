from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rag_center.modules.indexing.models.index_job import IndexJob


class IndexJobRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_for_tenant(self, job_id: str, tenant_id: str) -> IndexJob | None:
        return await self.session.scalar(
            select(IndexJob).where(IndexJob.id == job_id, IndexJob.tenant_id == tenant_id)
        )
