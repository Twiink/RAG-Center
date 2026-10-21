import asyncio
from uuid import NAMESPACE_URL, uuid5

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from rag_center.core.config import get_settings
from rag_center.core.exceptions import AppError, ErrorCode
from rag_center.core.index_versions import LEGACY_INDEX_VERSION
from rag_center.core.logging import get_logger
from rag_center.infrastructure.ids import generate_uuid
from rag_center.infrastructure.providers.embedding.base import EmbeddingProvider
from rag_center.infrastructure.providers.embedding.openai_compatible import (
    OpenAICompatibleEmbeddingProvider,
)
from rag_center.infrastructure.providers.keyword_search.base import KeywordSearchProvider
from rag_center.infrastructure.providers.keyword_search.elasticsearch import (
    ElasticsearchKeywordSearchProvider,
)
from rag_center.infrastructure.providers.vectorstores.base import VectorStore
from rag_center.infrastructure.providers.vectorstores.pgvector import PgVectorStore
from rag_center.modules.documents.markdown_splitter import (
    MarkdownStructuredSplitter,
    SplitPiece,
    build_retrieval_text,
)
from rag_center.modules.documents.models.document import Document
from rag_center.modules.documents.repositories.document_repository import DocumentRepository
from rag_center.modules.documents.schemas.document import UploadDocumentRequest
from rag_center.modules.documents.services.document_ingestion import prepare_document_content
from rag_center.modules.documents.status import DocumentStatus
from rag_center.modules.indexing.models.chunk import Chunk
from rag_center.modules.indexing.repositories.chunk_repository import ChunkRepository
from rag_center.modules.indexing.repositories.index_version_repository import IndexVersionRepository
from rag_center.modules.knowledge_bases.repositories.knowledge_base_repository import (
    KnowledgeBaseRepository,
)

logger = get_logger(__name__)

RETRYABLE_ERROR_CODES = {
    ErrorCode.API_TIMEOUT.code,
    ErrorCode.API_REQUEST_ERROR.code,
    ErrorCode.EMBEDDING_FAILED.code,
    ErrorCode.SYSTEM_BUSY.code,
}


def create_indexing_service(db: AsyncSession) -> "IndexingService":
    keyword_search_provider = None
    if (
        get_settings().elasticsearch_enabled
        and get_settings().keyword_search_provider == "elasticsearch"
    ):
        keyword_search_provider = ElasticsearchKeywordSearchProvider()

    return IndexingService(
        db,
        embedding_provider=OpenAICompatibleEmbeddingProvider(),
        vector_store=PgVectorStore(db),
        keyword_search_provider=keyword_search_provider,
        text_splitter=MarkdownStructuredSplitter(
            chunk_size=get_settings().chunk_size,
            chunk_overlap=get_settings().chunk_overlap,
            table_max_rows_per_chunk=get_settings().table_max_rows_per_chunk,
        ),
    )


class IndexingService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        embedding_provider: EmbeddingProvider,
        vector_store: VectorStore,
        keyword_search_provider: KeywordSearchProvider | None = None,
        text_splitter: MarkdownStructuredSplitter,
    ) -> None:
        self.db = db
        self.embedding_provider = embedding_provider
        self.vector_store = vector_store
        self.keyword_search_provider = keyword_search_provider
        self.text_splitter = text_splitter
        self.knowledge_base_repository = KnowledgeBaseRepository(db)
        self.document_repository = DocumentRepository(db)
        self.chunk_repository = ChunkRepository(db)
        self.index_version_repository = IndexVersionRepository(db)

    @classmethod
    def create_default(
        cls, db: AsyncSession, settings=None, http_clients=None
    ) -> "IndexingService":
        """按配置组装默认 Provider；settings 可为空以便测试注入。"""

        from rag_center.core.config import get_settings

        active_settings = settings or get_settings()
        keyword_search_provider = None
        if (
            active_settings.elasticsearch_enabled
            and active_settings.keyword_search_provider == "elasticsearch"
        ):
            keyword_search_provider = ElasticsearchKeywordSearchProvider(
                client=http_clients.elasticsearch if http_clients else None
            )
        return cls(
            db,
            embedding_provider=OpenAICompatibleEmbeddingProvider(
                client=http_clients.model if http_clients else None
            ),
            vector_store=PgVectorStore(db),
            keyword_search_provider=keyword_search_provider,
            text_splitter=MarkdownStructuredSplitter(
                chunk_size=active_settings.chunk_size,
                chunk_overlap=active_settings.chunk_overlap,
                table_max_rows_per_chunk=active_settings.table_max_rows_per_chunk,
            ),
        )

    def set_providers(
        self,
        *,
        embedding_provider: EmbeddingProvider,
        text_splitter: MarkdownStructuredSplitter,
        vector_store: VectorStore | None = None,
        keyword_search_provider: KeywordSearchProvider | None = None,
    ) -> None:
        """测试或替代运行时注入 Provider。"""

        self.embedding_provider = embedding_provider
        self.text_splitter = text_splitter
        if vector_store is not None:
            self.vector_store = vector_store
        if keyword_search_provider is not None:
            self.keyword_search_provider = keyword_search_provider

    async def create_document_record(
        self, tenant_id: str, request: UploadDocumentRequest
    ) -> Document:
        logger.info(
            "BUSINESS_NODE | create_document_record_start | tenant_id=%s | kb_id=%s | title=%s | content_chars=%s",
            tenant_id,
            request.kb_id,
            request.title,
            len(request.content),
        )
        knowledge_base = await self.knowledge_base_repository.get_by_id_and_tenant(
            request.kb_id,
            tenant_id,
        )
        if knowledge_base is None:
            raise AppError(
                ErrorCode.KNOWLEDGE_BASE_NOT_FOUND,
                context={"tenant_id": tenant_id, "kb_id": request.kb_id},
            )
        await self._ensure_not_building(tenant_id, request.kb_id)

        document = Document(
            id=generate_uuid(),
            tenant_id=tenant_id,
            kb_id=request.kb_id,
            title=request.title,
            content=request.content,
            source_type="text",
            status=DocumentStatus.PROCESSING,
        )
        await self.document_repository.create(document)

        logger.info(
            "BUSINESS_NODE | create_document_record_success | document_id=%s | kb_id=%s",
            document.id,
            document.kb_id,
        )
        return document

    async def create_file_document_record(
        self,
        tenant_id: str,
        *,
        kb_id: str,
        title: str,
        source_file_path: str,
        source_filename: str,
        document_id: str | None = None,
    ) -> Document:
        logger.info(
            "BUSINESS_NODE | create_file_document_record_start | tenant_id=%s | kb_id=%s | "
            "title=%s | source_filename=%s",
            tenant_id,
            kb_id,
            title,
            source_filename,
        )
        knowledge_base = await self.knowledge_base_repository.get_by_id_and_tenant(kb_id, tenant_id)
        if knowledge_base is None:
            raise AppError(
                ErrorCode.KNOWLEDGE_BASE_NOT_FOUND,
                context={"tenant_id": tenant_id, "kb_id": kb_id},
            )
        await self._ensure_not_building(tenant_id, kb_id)

        document = Document(
            id=document_id or generate_uuid(),
            tenant_id=tenant_id,
            kb_id=kb_id,
            title=title,
            content="",
            source_type="text",
            source_file_path=source_file_path,
            source_filename=source_filename,
            status=DocumentStatus.PROCESSING,
        )
        await self.document_repository.create(document)

        logger.info(
            "BUSINESS_NODE | create_file_document_record_success | document_id=%s | kb_id=%s",
            document.id,
            document.kb_id,
        )
        return document

    async def index_existing_document(self, document_id: str, *, reparse: bool = False) -> int:
        document = await self.document_repository.get_by_id(document_id)
        if document is None:
            logger.warning(
                "BUSINESS_NODE | index_existing_document_skip | document_id=%s | reason=not_found",
                document_id,
            )
            return 0

        if document.status != DocumentStatus.PROCESSING:
            logger.info(
                "BUSINESS_NODE | index_existing_document_skip | document_id=%s | status=%s",
                document_id,
                document.status,
            )
            return 0

        logger.info(
            "BUSINESS_NODE | index_existing_document_start | document_id=%s | kb_id=%s | title=%s",
            document.id,
            document.kb_id,
            document.title,
        )

        needs_prepare = bool(document.source_file_path) and (
            reparse or not document.content.strip()
        )
        if needs_prepare:
            try:
                parsed = await prepare_document_content(
                    content=document.content if not reparse and document.content.strip() else None,
                    file_path=document.source_file_path,
                    filename=document.source_filename,
                )
                document.content = parsed.content
                document.source_type = parsed.source_type
                await self.db.commit()
                await self.db.refresh(document)
            except AppError as exc:
                await self.db.rollback()
                await self._mark_failed(document, exc.msg)
                logger.exception(
                    "BUSINESS_NODE | index_existing_document_parse_failed | document_id=%s | "
                    "code=%s | msg=%s",
                    document.id,
                    exc.code,
                    exc.msg,
                )
                return 0
            except Exception as exc:
                await self.db.rollback()
                await self._mark_failed(document, f"文档解析失败: {exc}")
                logger.exception(
                    "BUSINESS_NODE | index_existing_document_parse_unhandled | document_id=%s",
                    document.id,
                )
                return 0

        if not document.content.strip():
            await self._mark_failed(document, ErrorCode.DOCUMENT_CONTENT_EMPTY.msg)
            return 0

        try:
            knowledge_base = await self.knowledge_base_repository.get_by_id(document.kb_id)
            if knowledge_base is None:
                raise AppError(ErrorCode.KNOWLEDGE_BASE_NOT_FOUND)
            chunk_count = await self._index_document_content(
                document,
                index_version=knowledge_base.active_index_version,
            )
            await self.document_repository.update_status(document, status=DocumentStatus.SUCCESS)
            await self.db.commit()
            logger.info(
                "BUSINESS_NODE | index_existing_document_success | document_id=%s | kb_id=%s | "
                "chunk_count=%s | keyword_search=%s",
                document.id,
                document.kb_id,
                chunk_count,
                self.keyword_search_provider.name if self.keyword_search_provider else None,
            )
            return chunk_count
        except AppError as exc:
            await self.db.rollback()
            if exc.code in RETRYABLE_ERROR_CODES:
                logger.warning(
                    "BUSINESS_NODE | index_existing_document_retryable | document_id=%s | code=%s | msg=%s",
                    document.id,
                    exc.code,
                    exc.msg,
                )
                raise
            await self._mark_failed(document, exc.msg)
            logger.exception(
                "BUSINESS_NODE | index_existing_document_failed | document_id=%s | code=%s | msg=%s",
                document.id,
                exc.code,
                exc.msg,
            )
            return 0
        except Exception as exc:
            await self.db.rollback()
            if isinstance(
                exc,
                (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError, ConnectionError),
            ):
                logger.warning(
                    "BUSINESS_NODE | index_existing_document_retryable | document_id=%s | error=%s",
                    document.id,
                    str(exc),
                )
                raise
            logger.exception(
                "BUSINESS_NODE | index_existing_document_unhandled | document_id=%s",
                document.id,
            )
            await self._mark_failed(document, str(exc))
            return 0

    async def index_document_version(self, document: Document, *, index_version: str) -> int:
        if not document.content.strip():
            raise AppError(ErrorCode.DOCUMENT_CONTENT_EMPTY)
        return await self._index_document_content(document, index_version=index_version)

    async def prepare_chunks(
        self, document: Document, *, index_version: str, generation: str
    ) -> tuple[list[dict], list[Chunk]]:
        """在线程池分块并调用模型；仅生成当前代次 DTO，不操作数据库。"""
        pieces: list[SplitPiece] = await asyncio.to_thread(
            self.text_splitter.split,
            document.content,
            document_id=document.id,
            index_version=index_version,
        )
        if not pieces:
            raise AppError(ErrorCode.DOCUMENT_CONTENT_EMPTY)

        logger.info(
            "BUSINESS_NODE | document_split_success | document_id=%s | chunk_count=%s",
            document.id,
            len(pieces),
        )

        is_contextual_index = index_version != LEGACY_INDEX_VERSION
        searchable_pieces: list[SplitPiece] = []
        pointer_pieces: list[SplitPiece] = []
        for piece in pieces:
            if piece.metadata.get("chunk_type") == "reference_pointer":
                pointer_pieces.append(piece)
            else:
                searchable_pieces.append(piece)

        retrieval_texts = [
            build_retrieval_text(
                title=document.title,
                heading_path=str(piece.metadata.get("heading_path") or ""),
                content=piece.text,
            )
            for piece in searchable_pieces
        ]
        texts = (
            retrieval_texts if is_contextual_index else [piece.text for piece in searchable_pieces]
        )
        embeddings = await self.embedding_provider.embed_texts(texts) if searchable_pieces else []

        searchable_chunks: list[dict] = []
        for index, piece in enumerate(searchable_pieces):
            retrieval_text = retrieval_texts[index] if is_contextual_index else None
            embedding = embeddings[index]
            piece_metadata = dict(piece.metadata)
            raw_section_id = piece_metadata.pop("section_id", None)
            raw_parent_section_id = piece_metadata.pop("parent_section_id", None)
            raw_order_index = piece_metadata.pop("order_index", None)
            section_id = raw_section_id if is_contextual_index else None
            parent_section_id = raw_parent_section_id if is_contextual_index else None
            order_index = raw_order_index if is_contextual_index else None
            piece_metadata["parser"] = document.source_type
            piece_metadata["generation"] = generation
            searchable_chunks.append(
                {
                    "chunk_id": str(
                        uuid5(
                            NAMESPACE_URL,
                            f"{generation}:{document.id}:{index_version}:search:{index}",
                        )
                    ),
                    "tenant_id": document.tenant_id,
                    "kb_id": document.kb_id,
                    "document_id": document.id,
                    "title": document.title,
                    "content": piece.text,
                    "retrieval_text": retrieval_text,
                    "index_version": index_version,
                    "section_id": section_id,
                    "parent_section_id": parent_section_id,
                    "order_index": order_index,
                    "metadata": {
                        "chunk_index": index,
                        "source_type": document.source_type,
                        **piece_metadata,
                    },
                    "embedding": embedding,
                }
            )

        pointer_db_chunks: list[Chunk] = []
        zero_embedding = [0.0] * get_settings().embedding_dimensions
        for index, piece in enumerate(pointer_pieces):
            piece_metadata = dict(piece.metadata)
            raw_section_id = piece_metadata.pop("section_id", None)
            raw_parent_section_id = piece_metadata.pop("parent_section_id", None)
            raw_order_index = piece_metadata.pop("order_index", None)
            section_id = raw_section_id if is_contextual_index else None
            parent_section_id = raw_parent_section_id if is_contextual_index else None
            order_index = raw_order_index if is_contextual_index else None
            piece_metadata["parser"] = document.source_type
            piece_metadata["generation"] = generation
            pointer_db_chunks.append(
                Chunk(
                    id=str(
                        uuid5(
                            NAMESPACE_URL,
                            f"{generation}:{document.id}:{index_version}:pointer:{index}",
                        )
                    ),
                    tenant_id=document.tenant_id,
                    kb_id=document.kb_id,
                    document_id=document.id,
                    title=document.title,
                    content=piece.text,
                    retrieval_text=None,
                    index_version=index_version,
                    section_id=section_id,
                    parent_section_id=parent_section_id,
                    order_index=order_index,
                    metadata_={
                        "chunk_index": len(searchable_pieces) + index,
                        "source_type": document.source_type,
                        **piece_metadata,
                    },
                    embedding=zero_embedding,
                )
            )

        return searchable_chunks, pointer_db_chunks

    async def _index_document_content(self, document: Document, *, index_version: str) -> int:
        searchable_chunks, pointer_db_chunks = await self.prepare_chunks(
            document, index_version=index_version, generation=generate_uuid()
        )
        if searchable_chunks:
            await self.vector_store.add_chunks(searchable_chunks)
            if self.keyword_search_provider is not None:
                await self.keyword_search_provider.add_chunks(searchable_chunks)
        if pointer_db_chunks:
            await self.chunk_repository.add_many(pointer_db_chunks)
        return len(searchable_chunks) + len(pointer_db_chunks)

    async def _ensure_not_building(self, tenant_id: str, kb_id: str) -> None:
        await self.knowledge_base_repository.get_by_id_and_tenant(kb_id, tenant_id, for_update=True)
        if await self.index_version_repository.get_building(tenant_id, kb_id) is not None:
            raise AppError(
                ErrorCode.DOCUMENT_PROCESSING,
                msg="知识库正在构建新索引，暂时不能修改文档",
                context={"tenant_id": tenant_id, "kb_id": kb_id},
            )

    async def _mark_failed(self, document: Document, error_message: str) -> None:
        await self.document_repository.update_status(
            document,
            status=DocumentStatus.FAILED,
            error_message=error_message,
        )
        await self.db.commit()
