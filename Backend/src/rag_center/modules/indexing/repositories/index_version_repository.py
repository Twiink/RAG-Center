from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rag_center.modules.indexing.models.index_version import KnowledgeBaseIndexVersion


class IndexVersionRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, item: KnowledgeBaseIndexVersion) -> KnowledgeBaseIndexVersion:
        self.db.add(item)
        await self.db.flush()
        return item

    async def get(
        self, tenant_id: str, kb_id: str, version: str
    ) -> KnowledgeBaseIndexVersion | None:
        stmt = select(KnowledgeBaseIndexVersion).where(
            KnowledgeBaseIndexVersion.tenant_id == tenant_id,
            KnowledgeBaseIndexVersion.kb_id == kb_id,
            KnowledgeBaseIndexVersion.version == version,
        )
        return await self.db.scalar(stmt)

    async def get_building(self, tenant_id: str, kb_id: str) -> KnowledgeBaseIndexVersion | None:
        stmt = select(KnowledgeBaseIndexVersion).where(
            KnowledgeBaseIndexVersion.tenant_id == tenant_id,
            KnowledgeBaseIndexVersion.kb_id == kb_id,
            KnowledgeBaseIndexVersion.status == "building",
        )
        return await self.db.scalar(stmt)

    async def list_by_kb(self, tenant_id: str, kb_id: str) -> list[KnowledgeBaseIndexVersion]:
        stmt = (
            select(KnowledgeBaseIndexVersion)
            .where(
                KnowledgeBaseIndexVersion.tenant_id == tenant_id,
                KnowledgeBaseIndexVersion.kb_id == kb_id,
            )
            .order_by(KnowledgeBaseIndexVersion.created_at.desc())
        )
        return list((await self.db.scalars(stmt)).all())

    async def delete(self, item: KnowledgeBaseIndexVersion) -> None:
        await self.db.delete(item)
        await self.db.flush()
