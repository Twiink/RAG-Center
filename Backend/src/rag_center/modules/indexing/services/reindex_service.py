from sqlalchemy.ext.asyncio import AsyncSession

from rag_center.core.config import get_settings
from rag_center.core.logging import get_logger
from rag_center.modules.documents.models.document import Document
from rag_center.modules.documents.repositories.document_repository import DocumentRepository
from rag_center.modules.documents.status import DocumentStatus
from rag_center.modules.indexing.services.jobs import QueueIndexJobResult, queue_index_job
from rag_center.modules.knowledge_bases.repositories.knowledge_base_repository import (
    KnowledgeBaseRepository,
)

logger = get_logger(__name__)


async def queue_document_reindex(
    db: AsyncSession, document: Document, *, reparse: bool = False
) -> QueueIndexJobResult:
    if document.status == DocumentStatus.PROCESSING:
        raise ValueError(f"document is processing: {document.id}")

    prior_status = document.status
    knowledge_base = await KnowledgeBaseRepository(db).get_by_id(document.kb_id)
    if knowledge_base is None:
        raise ValueError(f"knowledge base not found: {document.kb_id}")
    await DocumentRepository(db).update_status(
        document,
        status=DocumentStatus.PROCESSING,
        error_message=None,
    )
    if reparse:
        document.content = ""
        await db.flush()
    job = await queue_index_job(
        db,
        tenant_id=document.tenant_id,
        resource_id=document.id,
        job_type="document",
        payload={"document_id": document.id, "reparse": reparse},
        max_attempts=get_settings().index_job_max_attempts,
        lease_seconds=get_settings().index_job_lease_seconds,
    )
    logger.info(
        "BUSINESS_NODE | queue_document_reindex | document_id=%s | kb_id=%s | prior_status=%s",
        document.id,
        document.kb_id,
        prior_status,
    )
    return job
