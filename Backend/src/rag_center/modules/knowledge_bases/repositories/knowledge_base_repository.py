from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from rag_center.modules.documents.models.document import Document
from rag_center.modules.indexing.models.chunk import Chunk
from rag_center.modules.knowledge_bases.models.knowledge_base import KnowledgeBase


class KnowledgeBaseRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, knowledge_base: KnowledgeBase) -> KnowledgeBase:
        self.db.add(knowledge_base)
        await self.db.flush()
        return knowledge_base

    async def get_by_id(self, kb_id: str) -> KnowledgeBase | None:
        stmt = select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
        return await self.db.scalar(stmt)

    async def get_by_id_and_tenant(
        self, kb_id: str, tenant_id: str, *, for_update: bool = False
    ) -> KnowledgeBase | None:
        stmt = select(KnowledgeBase).where(
            KnowledgeBase.id == kb_id,
            KnowledgeBase.tenant_id == tenant_id,
        )
        if for_update:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        return await self.db.scalar(stmt)

    async def list_for_tree(
        self, tenant_id: str, keyword: str | None = None
    ) -> list[KnowledgeBase]:
        stmt = (
            select(KnowledgeBase)
            .where(KnowledgeBase.tenant_id == tenant_id)
            .order_by(KnowledgeBase.created_at.desc())
            .options(selectinload(KnowledgeBase.documents))
        )
        if keyword:
            stmt = stmt.where(KnowledgeBase.name.ilike(f"%{keyword}%"))
        return list((await self.db.scalars(stmt)).all())

    async def update_settings(self, kb_id: str, settings: dict) -> KnowledgeBase | None:
        knowledge_base = await self.get_by_id(kb_id)
        if knowledge_base is None:
            return None
        knowledge_base.settings = settings
        await self.db.flush()
        return knowledge_base

    async def count_documents(self, kb_id: str) -> int:
        stmt = select(func.count()).select_from(Document).where(Document.kb_id == kb_id)
        return int(await self.db.scalar(stmt) or 0)

    async def count_by_tenant(self, tenant_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(KnowledgeBase)
            .where(
                KnowledgeBase.tenant_id == tenant_id,
            )
        )
        return int(await self.db.scalar(stmt) or 0)

    async def delete(self, knowledge_base: KnowledgeBase) -> None:
        await self.db.delete(knowledge_base)
        await self.db.flush()

    async def get_chunk_counts(self, index_versions: dict[str, str]) -> dict[str, int]:
        if not index_versions:
            return {}
        version_scopes = [
            and_(
                Chunk.document_id == document_id,
                Chunk.index_version == index_version,
            )
            for document_id, index_version in index_versions.items()
        ]
        stmt = (
            select(Chunk.document_id, func.count())
            .where(or_(*version_scopes))
            .group_by(Chunk.document_id)
        )
        rows = (await self.db.execute(stmt)).all()
        return {str(document_id): int(count) for document_id, count in rows}

    async def get_chunk_counts_by_version(
        self, document_ids: list[str]
    ) -> dict[tuple[str, str], int]:
        if not document_ids:
            return {}
        stmt = (
            select(Chunk.document_id, Chunk.index_version, func.count())
            .where(Chunk.document_id.in_(document_ids))
            .group_by(Chunk.document_id, Chunk.index_version)
        )
        rows = (await self.db.execute(stmt)).all()
        return {
            (str(document_id), str(index_version)): int(count)
            for document_id, index_version, count in rows
        }
