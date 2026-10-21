from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from rag_center.core.config import get_settings
from rag_center.core.exceptions import AppError, ErrorCode
from rag_center.core.index_versions import DEFAULT_INDEX_VERSION
from rag_center.core.logging import get_logger
from rag_center.infrastructure.ids import generate_uuid
from rag_center.infrastructure.providers.keyword_search.elasticsearch import (
    ElasticsearchKeywordSearchProvider,
)
from rag_center.modules.documents.repositories.document_repository import DocumentRepository
from rag_center.modules.documents.status import DocumentStatus
from rag_center.modules.indexing.models.index_version import KnowledgeBaseIndexVersion
from rag_center.modules.indexing.repositories.chunk_relation_repository import (
    ChunkRelationRepository,
)
from rag_center.modules.indexing.repositories.chunk_repository import ChunkRepository
from rag_center.modules.indexing.repositories.index_version_repository import IndexVersionRepository
from rag_center.modules.indexing.services.indexing_service import create_indexing_service
from rag_center.modules.indexing.services.jobs import queue_index_job
from rag_center.modules.indexing.services.reference_relation_service import ReferenceRelationService
from rag_center.modules.knowledge_bases.repositories.knowledge_base_repository import (
    KnowledgeBaseRepository,
)
from rag_center.modules.knowledge_bases.schemas.knowledge_base import (
    IndexVersionData,
    RebuildIndexData,
)

logger = get_logger(__name__)
VERSION_RE = re.compile(r"^v(\d+)$")


def utc_now() -> datetime:
    return datetime.now(UTC)


class IndexVersionService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.knowledge_base_repository = KnowledgeBaseRepository(db)
        self.version_repository = IndexVersionRepository(db)
        self.document_repository = DocumentRepository(db)
        self.chunk_repository = ChunkRepository(db)
        self.relation_repository = ChunkRelationRepository(db)
        self.create_indexing_service = lambda: create_indexing_service(db)

    async def create_initial(
        self,
        *,
        tenant_id: str,
        kb_id: str,
        version: str = DEFAULT_INDEX_VERSION,
    ) -> None:
        await self.version_repository.create(
            KnowledgeBaseIndexVersion(
                id=generate_uuid(),
                tenant_id=tenant_id,
                kb_id=kb_id,
                version=version,
                status="active",
                document_count=0,
                chunk_count=0,
                completed_at=utc_now(),
                activated_at=utc_now(),
            )
        )

    async def list_versions(self, tenant_id: str, kb_id: str) -> list[IndexVersionData]:
        await self._get_kb_or_raise(tenant_id, kb_id)
        return [
            self._to_data(item)
            for item in await self.version_repository.list_by_kb(tenant_id, kb_id)
        ]

    async def queue_rebuild(self, tenant_id: str, kb_id: str) -> RebuildIndexData:
        data = await self.start_rebuild(tenant_id, kb_id)
        job = await queue_index_job(
            self.db,
            tenant_id=tenant_id,
            resource_id=kb_id,
            job_type="index_version",
            payload={"kb_id": kb_id, "version": data.version},
            max_attempts=get_settings().index_job_max_attempts,
            lease_seconds=get_settings().index_job_lease_seconds,
        )
        await self.db.commit()
        data.job_id = job.job_id
        return data

    async def start_rebuild(self, tenant_id: str, kb_id: str) -> RebuildIndexData:
        await self._get_kb_or_raise(tenant_id, kb_id, for_update=True)
        if await self.version_repository.get_building(tenant_id, kb_id) is not None:
            raise AppError(
                ErrorCode.DOCUMENT_PROCESSING,
                msg="该知识库已有索引版本正在构建",
                context={"tenant_id": tenant_id, "kb_id": kb_id},
            )
        if await self.document_repository.list_by_kb_id_and_status(
            kb_id, DocumentStatus.PROCESSING
        ):
            raise AppError(ErrorCode.DOCUMENT_PROCESSING, msg="知识库下仍有文档正在索引")
        version = await self._next_version(tenant_id, kb_id)
        item = await self.version_repository.create(
            KnowledgeBaseIndexVersion(
                id=generate_uuid(),
                tenant_id=tenant_id,
                kb_id=kb_id,
                version=version,
                status="building",
                document_count=0,
                chunk_count=0,
            )
        )
        return RebuildIndexData(kb_id=kb_id, version=item.version, status=item.status)

    async def rebuild(self, tenant_id: str, kb_id: str, version: str) -> None:
        item = await self.version_repository.get(tenant_id, kb_id, version)
        if item is None or item.status != "building":
            return
        documents = await self.document_repository.list_by_kb_id(kb_id)
        documents = [
            document
            for document in documents
            if document.tenant_id == tenant_id
            and document.status == DocumentStatus.SUCCESS
            and document.content.strip()
        ]
        try:
            indexing_service = self.create_indexing_service()
            for document in documents:
                await indexing_service.index_document_version(document, index_version=version)
            relation_count = await ReferenceRelationService(self.db).rebuild(
                tenant_id=tenant_id,
                kb_id=kb_id,
                index_version=version,
            )
            item.document_count = len(documents)
            item.chunk_count = await self.chunk_repository.count_by_kb_id(
                tenant_id, kb_id, index_version=version
            )
            item.status = "ready"
            item.completed_at = utc_now()
            item.error_message = None
            await self.db.commit()
            logger.info(
                "INDEX_VERSION_READY | tenant_id=%s | kb_id=%s | version=%s | "
                "document_count=%s | chunk_count=%s | relation_count=%s",
                tenant_id,
                kb_id,
                version,
                item.document_count,
                item.chunk_count,
                relation_count,
            )
        except Exception as exc:
            await self.db.rollback()
            await self.chunk_repository.delete_by_kb_version(tenant_id, kb_id, version)
            await self.relation_repository.delete_by_kb_version(tenant_id, kb_id, version)
            await self.db.commit()
            if get_settings().keyword_search_provider == "elasticsearch":
                try:
                    await ElasticsearchKeywordSearchProvider().delete_by_kb_version(
                        tenant_id=tenant_id,
                        kb_id=kb_id,
                        index_version=version,
                    )
                except Exception:
                    logger.exception(
                        "INDEX_VERSION_ES_CLEANUP_FAILED | tenant_id=%s | kb_id=%s | version=%s",
                        tenant_id,
                        kb_id,
                        version,
                    )
            item = await self.version_repository.get(tenant_id, kb_id, version)
            if item is not None:
                item.status = "failed"
                item.error_message = str(exc)[:2000]
                item.completed_at = utc_now()
                await self.db.commit()
            raise

    async def activate(self, tenant_id: str, kb_id: str, version: str) -> IndexVersionData:
        knowledge_base = await self._get_kb_or_raise(tenant_id, kb_id, for_update=True)
        target = await self.version_repository.get(tenant_id, kb_id, version)
        if target is None:
            raise AppError(ErrorCode.NOT_FOUND, msg="索引版本不存在")
        if target.status == "active":
            return self._to_data(target)
        if target.status not in {"ready", "archived"}:
            raise AppError(
                ErrorCode.PARAM_ERROR,
                msg="只有已就绪或已归档的索引版本可以激活",
                context={"status": target.status, "version": version},
            )

        current = await self.version_repository.get(
            tenant_id, kb_id, knowledge_base.active_index_version
        )
        if current is not None and current.status == "active":
            current.status = "archived"
            await self.db.flush()
        target.status = "active"
        target.activated_at = utc_now()
        knowledge_base.active_index_version = version
        await self.db.commit()
        await self.db.refresh(target)
        return self._to_data(target)

    async def delete_version(self, tenant_id: str, kb_id: str, version: str) -> None:
        knowledge_base = await self._get_kb_or_raise(tenant_id, kb_id, for_update=True)
        item = await self.version_repository.get(tenant_id, kb_id, version)
        if item is None:
            raise AppError(ErrorCode.NOT_FOUND, msg="索引版本不存在")
        if knowledge_base.active_index_version == version or item.status == "active":
            raise AppError(ErrorCode.PARAM_ERROR, msg="活动索引版本不能删除")
        if item.status == "building":
            raise AppError(ErrorCode.DOCUMENT_PROCESSING, msg="正在构建的索引版本不能删除")

        await self.relation_repository.delete_by_kb_version(tenant_id, kb_id, version)
        await self.chunk_repository.delete_by_kb_version(tenant_id, kb_id, version)
        await self.version_repository.delete(item)
        if get_settings().keyword_search_provider == "elasticsearch":
            await ElasticsearchKeywordSearchProvider().delete_by_kb_version(
                tenant_id=tenant_id,
                kb_id=kb_id,
                index_version=version,
            )
        await self.db.commit()

    async def ensure_not_building(self, tenant_id: str, kb_id: str) -> None:
        await self._get_kb_or_raise(tenant_id, kb_id, for_update=True)
        if await self.version_repository.get_building(tenant_id, kb_id) is not None:
            raise AppError(
                ErrorCode.DOCUMENT_PROCESSING,
                msg="知识库正在构建新索引，暂时不能修改文档",
                context={"tenant_id": tenant_id, "kb_id": kb_id},
            )

    async def _next_version(self, tenant_id: str, kb_id: str) -> str:
        numbers = []
        for item in await self.version_repository.list_by_kb(tenant_id, kb_id):
            match = VERSION_RE.match(item.version)
            if match:
                numbers.append(int(match.group(1)))
        return f"v{max(numbers, default=1) + 1}"

    async def _get_kb_or_raise(self, tenant_id: str, kb_id: str, *, for_update: bool = False):
        knowledge_base = await self.knowledge_base_repository.get_by_id_and_tenant(
            kb_id, tenant_id, for_update=for_update
        )
        if knowledge_base is None:
            raise AppError(
                ErrorCode.KNOWLEDGE_BASE_NOT_FOUND,
                context={"tenant_id": tenant_id, "kb_id": kb_id},
            )
        return knowledge_base

    @staticmethod
    def _to_data(item: KnowledgeBaseIndexVersion) -> IndexVersionData:
        return IndexVersionData(
            version=item.version,
            status=item.status,
            document_count=item.document_count,
            chunk_count=item.chunk_count,
            error_message=item.error_message,
            created_at=item.created_at,
            completed_at=item.completed_at,
            activated_at=item.activated_at,
        )
