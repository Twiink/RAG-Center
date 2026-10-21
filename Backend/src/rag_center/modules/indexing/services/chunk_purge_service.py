from sqlalchemy.ext.asyncio import AsyncSession

from rag_center.core.config import get_settings
from rag_center.core.logging import get_logger
from rag_center.infrastructure.providers.keyword_search.elasticsearch import (
    ElasticsearchKeywordSearchProvider,
)
from rag_center.modules.indexing.repositories.chunk_repository import ChunkRepository

logger = get_logger(__name__)


async def purge_document_chunks(
    db: AsyncSession, document_id: str, *, index_version: str | None = None
) -> None:
    await ChunkRepository(db).delete_by_document_id(document_id, index_version=index_version)
    if get_settings().keyword_search_provider == "elasticsearch":
        await ElasticsearchKeywordSearchProvider().delete_by_document_id(
            document_id,
            index_version=index_version,
        )
    logger.info(
        "BUSINESS_NODE | purge_document_chunks_success | document_id=%s | "
        "index_version=%s | keyword_search=%s",
        document_id,
        index_version,
        get_settings().keyword_search_provider,
    )
