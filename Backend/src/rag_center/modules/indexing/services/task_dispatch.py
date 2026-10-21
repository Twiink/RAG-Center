from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from rag_center.core.config import Settings
from rag_center.core.container import ResourceContainer
from rag_center.core.exceptions import AppError, ErrorCode
from rag_center.modules.documents.models.document import Document
from rag_center.modules.documents.services.document_ingestion import prepare_document_content
from rag_center.modules.documents.status import DocumentStatus
from rag_center.modules.indexing.models.chunk import Chunk
from rag_center.modules.indexing.models.index_job import IndexJob
from rag_center.modules.indexing.models.index_version import KnowledgeBaseIndexVersion
from rag_center.modules.indexing.services.indexing_service import IndexingService
from rag_center.modules.indexing.services.job_executor import (
    PermanentIndexJobError,
    execute_index_job,
    lock_valid_lease,
)
from rag_center.modules.indexing.services.reference_relation_service import ReferenceRelationService
from rag_center.modules.knowledge_bases.models.knowledge_base import KnowledgeBase

RETRYABLE_CODES = {
    ErrorCode.EMBEDDING_FAILED.code,
    ErrorCode.API_TIMEOUT.code,
    ErrorCode.API_REQUEST_ERROR.code,
    ErrorCode.SYSTEM_BUSY.code,
    ErrorCode.KEYWORD_SEARCH_ERROR.code,
}


def _snapshot(document: Document) -> Document:
    """复制任务所需数据；外部 I/O 期间不保留 ORM 实例或数据库事务。"""
    return Document(
        **{
            key: getattr(document, key)
            for key in (
                "id",
                "tenant_id",
                "kb_id",
                "title",
                "content",
                "source_type",
                "source_file_path",
                "source_filename",
                "status",
            )
        }
    )


async def dispatch_index_job(
    session: AsyncSession,
    job: IndexJob,
    settings: Settings,
    container: ResourceContainer | None = None,
) -> None:
    """先准备带代次的产物，再持有效租约原子发布数据库产物及业务状态。"""
    if job.job_type not in {"document", "index_version"}:
        raise PermanentIndexJobError("不支持的索引任务类型")
    if job.job_type == "document":
        document = await session.scalar(
            select(Document).where(
                Document.id == job.resource_id, Document.tenant_id == job.tenant_id
            )
        )
        if document is None:
            raise PermanentIndexJobError("文档不存在")
        kb_id = document.kb_id
        documents = [_snapshot(document)]
    else:
        kb_id = job.resource_id
        documents = [
            _snapshot(item)
            for item in (
                await session.scalars(
                    select(Document).where(
                        Document.kb_id == kb_id,
                        Document.tenant_id == job.tenant_id,
                        Document.status == DocumentStatus.SUCCESS,
                    )
                )
            ).all()
        ]
    kb = await session.scalar(
        select(KnowledgeBase).where(
            KnowledgeBase.id == kb_id, KnowledgeBase.tenant_id == job.tenant_id
        )
    )
    if kb is None:
        raise PermanentIndexJobError("知识库不存在")
    version = (
        str(job.payload.get("version", ""))
        if job.job_type == "index_version"
        else kb.active_index_version
    )
    if not version:
        raise PermanentIndexJobError("缺少索引版本")
    await session.rollback()

    service = IndexingService.create_default(
        session, settings, http_clients=container.external_clients if container else None
    )
    chunks: list[dict] = []
    pointers: list[Chunk] = []
    try:
        for document in documents:
            if job.job_type == "document":
                parsed = await prepare_document_content(
                    content=None if job.payload.get("reparse") else document.content,
                    file_path=document.source_file_path,
                    filename=document.source_filename,
                )
                document.content, document.source_type = parsed.content, parsed.source_type
            if not document.content.strip():
                raise PermanentIndexJobError("文档内容为空")
            prepared, prepared_pointers = await service.prepare_chunks(
                document, index_version=version, generation=f"{job.id}:{job.attempt}"
            )
            chunks.extend(prepared)
            pointers.extend(prepared_pointers)
        # ES 以代次稳定 ID 写入。检索必须以 PostgreSQL 中已发布 ID 二次校验。
        if service.keyword_search_provider and chunks:
            await service.keyword_search_provider.add_chunks(chunks)
    except AppError as exc:
        if exc.code not in RETRYABLE_CODES:
            raise PermanentIndexJobError(exc.msg) from exc
        raise

    await lock_valid_lease(session, job)
    current_kb = await session.scalar(
        select(KnowledgeBase)
        .where(KnowledgeBase.id == kb_id, KnowledgeBase.tenant_id == job.tenant_id)
        .with_for_update()
    )
    if current_kb is None:
        raise PermanentIndexJobError("知识库已删除")
    if job.job_type == "document" and current_kb.active_index_version != version:
        raise RuntimeError("索引版本已切换，请重新准备当前版本")
    scope = [Chunk.tenant_id == job.tenant_id, Chunk.kb_id == kb_id, Chunk.index_version == version]
    if job.job_type == "document":
        scope.append(Chunk.document_id == job.resource_id)
    await session.execute(delete(Chunk).where(*scope))
    await service.vector_store.add_chunks(chunks)
    if pointers:
        session.add_all(pointers)
    await session.flush()
    if job.job_type == "document":
        target = await session.scalar(
            select(Document)
            .where(Document.id == job.resource_id, Document.tenant_id == job.tenant_id)
            .with_for_update()
        )
        if target is None:
            raise PermanentIndexJobError("文档已删除")
        target.content, target.source_type = documents[0].content, documents[0].source_type
        target.status, target.error_message = DocumentStatus.SUCCESS, None
    else:
        target_version = await session.scalar(
            select(KnowledgeBaseIndexVersion)
            .where(
                KnowledgeBaseIndexVersion.tenant_id == job.tenant_id,
                KnowledgeBaseIndexVersion.kb_id == kb_id,
                KnowledgeBaseIndexVersion.version == version,
            )
            .with_for_update()
        )
        if target_version is None or target_version.status != "building":
            raise PermanentIndexJobError("索引版本不再处于构建状态")
        await ReferenceRelationService(session).rebuild(
            tenant_id=job.tenant_id, kb_id=kb_id, index_version=version
        )
        target_version.document_count = len(documents)
        target_version.chunk_count = len(chunks) + len(pointers)
        target_version.status, target_version.completed_at = "ready", datetime.now(UTC)
        target_version.error_message = None
    # 不在此提交：execute_index_job 将产物、资源状态与任务成功同时提交。


async def run_index_job(
    session: AsyncSession,
    job_id: str,
    settings: Settings,
    *,
    container: ResourceContainer | None = None,
):
    result = await execute_index_job(
        session,
        job_id=job_id,
        runner=lambda session, job: dispatch_index_job(session, job, settings, container),
        settings=settings,
        session_factory=container.session_factory if container else None,
    )

    if container is not None:
        container.metrics.jobs.labels("index", str(result)).inc()
    return result
