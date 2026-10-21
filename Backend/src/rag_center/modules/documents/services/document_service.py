import asyncio
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from rag_center.core.config import Settings
from rag_center.core.exceptions import AppError, ErrorCode
from rag_center.core.index_versions import DEFAULT_INDEX_VERSION
from rag_center.core.logging import get_logger
from rag_center.infrastructure.ids import generate_uuid
from rag_center.infrastructure.providers.parsers.registry import validate_upload_extension
from rag_center.infrastructure.storage.document_storage import save_document_file
from rag_center.modules.documents.repositories.document_repository import DocumentRepository
from rag_center.modules.documents.schemas.document import (
    DocumentDetailData,
    ReindexDocumentData,
    UploadDocumentData,
    UploadDocumentRequest,
)
from rag_center.modules.documents.status import DocumentStatus
from rag_center.modules.indexing.repositories.chunk_repository import ChunkRepository
from rag_center.modules.indexing.services.chunk_purge_service import purge_document_chunks
from rag_center.modules.indexing.services.index_version_service import IndexVersionService
from rag_center.modules.indexing.services.indexing_service import create_indexing_service
from rag_center.modules.indexing.services.jobs import queue_index_job
from rag_center.modules.indexing.services.reindex_service import queue_document_reindex
from rag_center.modules.knowledge_bases.repositories.knowledge_base_repository import (
    KnowledgeBaseRepository,
)
from rag_center.modules.tenants.services.quota_service import QuotaService

logger = get_logger(__name__)


class DocumentService:
    def __init__(self, db: AsyncSession, settings: Settings | None = None) -> None:
        from rag_center.core.config import get_settings

        self.settings = settings or get_settings()
        self.db = db
        self.document_repository = DocumentRepository(db)
        self.chunk_repository = ChunkRepository(db)
        self.knowledge_base_repository = KnowledgeBaseRepository(db)

    async def upload(self, tenant_id: str, request: UploadDocumentRequest) -> UploadDocumentData:
        await QuotaService(self.db).check_upload_document(tenant_id, request.kb_id)
        indexing_service = create_indexing_service(self.db)
        document = await indexing_service.create_document_record(tenant_id, request)
        job = await queue_index_job(
            self.db,
            tenant_id=tenant_id,
            resource_id=document.id,
            job_type="document",
            payload={"document_id": document.id},
            max_attempts=self.settings.index_job_max_attempts,
            lease_seconds=self.settings.index_job_lease_seconds,
        )
        await self.db.commit()
        return UploadDocumentData(
            document_id=document.id,
            kb_id=document.kb_id,
            status=DocumentStatus.PROCESSING,
            chunk_count=0,
            job_id=job.job_id,
        )

    async def upload_file(
        self,
        tenant_id: str,
        *,
        kb_id: str,
        title: str | None,
        filename: str,
        file_bytes: bytes,
    ) -> UploadDocumentData:
        validate_upload_extension(filename)
        max_bytes = self.settings.document_max_size_mb * 1024 * 1024
        if len(file_bytes) > max_bytes:
            raise AppError(
                ErrorCode.PARAM_ERROR,
                msg=f"文件大小超过限制（最大 {self.settings.document_max_size_mb} MB）",
                context={"filename": filename, "size_bytes": len(file_bytes)},
            )

        await QuotaService(self.db).check_upload_document(tenant_id, kb_id)
        indexing_service = create_indexing_service(self.db)

        document_title = (title or Path(filename).stem or filename)[:255]
        document_id = generate_uuid()
        source_file_path = await asyncio.to_thread(
            save_document_file,
            tenant_id=tenant_id,
            document_id=document_id,
            filename=filename,
            file_bytes=file_bytes,
        )
        document = await indexing_service.create_file_document_record(
            tenant_id,
            kb_id=kb_id,
            title=document_title,
            source_file_path=source_file_path,
            source_filename=Path(filename).name,
            document_id=document_id,
        )

        job = await queue_index_job(
            self.db,
            tenant_id=tenant_id,
            resource_id=document.id,
            job_type="document",
            payload={"document_id": document.id},
            max_attempts=self.settings.index_job_max_attempts,
            lease_seconds=self.settings.index_job_lease_seconds,
        )
        await self.db.commit()
        return UploadDocumentData(
            document_id=document.id,
            kb_id=document.kb_id,
            status=DocumentStatus.PROCESSING,
            chunk_count=0,
            job_id=job.job_id,
        )

    async def get_detail(self, tenant_id: str, document_id: str) -> DocumentDetailData:
        document = await self._get_document_or_raise(tenant_id, document_id)
        knowledge_base = await self.knowledge_base_repository.get_by_id_and_tenant(
            document.kb_id,
            tenant_id,
        )
        index_version = (
            knowledge_base.active_index_version if knowledge_base else DEFAULT_INDEX_VERSION
        )
        chunk_count = await self.chunk_repository.count_by_document_id(document_id, index_version)
        return DocumentDetailData(
            document_id=document.id,
            kb_id=document.kb_id,
            title=document.title,
            status=document.status,
            error_message=document.error_message,
            chunk_count=chunk_count,
            source_type=document.source_type,
            source_filename=document.source_filename,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )

    async def delete(self, tenant_id: str, document_id: str) -> None:
        document = await self._get_document_or_raise(tenant_id, document_id)
        await IndexVersionService(self.db).ensure_not_building(tenant_id, document.kb_id)
        if document.status == DocumentStatus.PROCESSING:
            raise AppError(
                ErrorCode.DOCUMENT_PROCESSING,
                context={"tenant_id": tenant_id, "document_id": document_id},
            )

        await purge_document_chunks(self.db, document_id)
        await self.document_repository.delete(document)
        await self.db.commit()
        logger.info(
            "BUSINESS_NODE | delete_document_success | tenant_id=%s | document_id=%s",
            tenant_id,
            document_id,
        )

    async def reindex(
        self, tenant_id: str, document_id: str, *, reparse: bool = False
    ) -> ReindexDocumentData:
        document = await self._get_document_or_raise(tenant_id, document_id)
        await IndexVersionService(self.db).ensure_not_building(tenant_id, document.kb_id)
        if document.status == DocumentStatus.PROCESSING:
            raise AppError(
                ErrorCode.DOCUMENT_PROCESSING,
                context={"tenant_id": tenant_id, "document_id": document_id},
            )
        if document.status not in (DocumentStatus.FAILED, DocumentStatus.SUCCESS):
            raise AppError(
                ErrorCode.PARAM_ERROR,
                msg="仅索引成功或失败的文档可重建索引",
                context={
                    "tenant_id": tenant_id,
                    "document_id": document_id,
                    "status": document.status,
                },
            )

        await QuotaService(self.db).check_reindex(tenant_id)

        job = await queue_document_reindex(self.db, document, reparse=reparse)
        await self.db.commit()
        logger.info(
            "BUSINESS_NODE | reindex_document_queued | tenant_id=%s | document_id=%s",
            tenant_id,
            document_id,
        )
        return ReindexDocumentData(
            document_id=document.id,
            kb_id=document.kb_id,
            status=DocumentStatus.PROCESSING,
            chunk_count=0,
            job_id=job.job_id,
        )

    async def _get_document_or_raise(self, tenant_id: str, document_id: str):
        document = await self.document_repository.get_by_id_and_tenant(document_id, tenant_id)
        if document is None:
            raise AppError(
                ErrorCode.DOCUMENT_NOT_FOUND,
                context={"tenant_id": tenant_id, "document_id": document_id},
            )
        return document
