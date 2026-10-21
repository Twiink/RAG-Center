"""将升级前没有可靠任务记录的处理中资源补入事务 outbox。"""

from sqlalchemy import exists, select

from rag_center.modules.documents.models.document import Document
from rag_center.modules.documents.status import DocumentStatus
from rag_center.modules.indexing.models.index_job import IndexJob
from rag_center.modules.indexing.models.index_version import KnowledgeBaseIndexVersion
from rag_center.modules.indexing.services.jobs import queue_index_job


async def recover_legacy_index_resources(session, settings) -> int:
    documents = (
        await session.scalars(
            select(Document)
            .where(
                Document.status == DocumentStatus.PROCESSING,
                ~exists(
                    select(IndexJob.id).where(
                        IndexJob.resource_id == Document.id, IndexJob.job_type == "document"
                    )
                ),
            )
            .limit(100)
            .with_for_update(skip_locked=True)
        )
    ).all()
    count = 0
    for document in documents:
        await queue_index_job(
            session,
            tenant_id=document.tenant_id,
            resource_id=document.id,
            job_type="document",
            payload={"document_id": document.id},
            max_attempts=settings.index_job_max_attempts,
            lease_seconds=settings.index_job_lease_seconds,
        )
        count += 1
    versions = (
        await session.scalars(
            select(KnowledgeBaseIndexVersion)
            .where(
                KnowledgeBaseIndexVersion.status == "building",
                ~exists(
                    select(IndexJob.id).where(
                        IndexJob.resource_id == KnowledgeBaseIndexVersion.kb_id,
                        IndexJob.job_type == "index_version",
                        IndexJob.payload["version"].astext == KnowledgeBaseIndexVersion.version,
                    )
                ),
                ~exists(
                    select(IndexJob.id).where(
                        IndexJob.resource_id == KnowledgeBaseIndexVersion.kb_id,
                        IndexJob.status.in_(["queued", "running"]),
                    )
                ),
            )
            .limit(100)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for version in versions:
        await queue_index_job(
            session,
            tenant_id=version.tenant_id,
            resource_id=version.kb_id,
            job_type="index_version",
            payload={"kb_id": version.kb_id, "version": version.version},
            max_attempts=settings.index_job_max_attempts,
            lease_seconds=settings.index_job_lease_seconds,
        )
        count += 1
    await session.commit()
    return count
