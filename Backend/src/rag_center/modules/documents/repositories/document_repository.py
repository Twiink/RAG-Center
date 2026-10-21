from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from rag_center.modules.documents.models.document import Document
from rag_center.modules.documents.status import DocumentStatus


class DocumentRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, document: Document) -> Document:
        self.db.add(document)
        await self.db.flush()
        return document

    async def get_by_id(self, document_id: str) -> Document | None:
        return await self.db.get(Document, document_id)

    async def get_by_id_and_tenant(self, document_id: str, tenant_id: str) -> Document | None:
        stmt = select(Document).where(
            Document.id == document_id,
            Document.tenant_id == tenant_id,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_by_kb_id(self, kb_id: str) -> list[Document]:
        stmt = select(Document).where(Document.kb_id == kb_id)
        return list((await self.db.scalars(stmt)).all())

    async def list_by_kb_id_and_status(self, kb_id: str, status: int) -> list[Document]:
        stmt = select(Document).where(Document.kb_id == kb_id, Document.status == status)
        return list((await self.db.scalars(stmt)).all())

    async def count_by_kb_id(self, kb_id: str) -> int:
        stmt = select(func.count()).select_from(Document).where(Document.kb_id == kb_id)
        return int(await self.db.scalar(stmt) or 0)

    async def count_processing_by_tenant(self, tenant_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(Document)
            .where(
                Document.tenant_id == tenant_id,
                Document.status == DocumentStatus.PROCESSING,
            )
        )
        return int(await self.db.scalar(stmt) or 0)

    async def update_status(
        self,
        document: Document,
        *,
        status: int,
        error_message: str | None = None,
    ) -> Document:
        document.status = status
        document.error_message = error_message
        await self.db.flush()
        return document

    async def delete(self, document: Document) -> None:
        await self.db.delete(document)
        await self.db.flush()
