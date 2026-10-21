from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from rag_center.core.config import Settings
from rag_center.core.logging import get_logger
from rag_center.modules.indexing.services.job_executor import recover_expired_index_jobs
from rag_center.modules.indexing.services.outbox import (
    MessagePublisher,
    publish_pending_index_outbox,
    repair_queued_deliveries,
)

logger = get_logger(__name__)


class OutboxScheduler:
    """周期性发布 outbox 消息并回收过期任务租约。"""

    def __init__(
        self, session_factory, settings: Settings, publisher: MessagePublisher, container=None
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.publisher = publisher
        self.container = container
        self.cleanup_cursor = None

    async def run_once(self) -> tuple[int, int]:
        async with self.session_factory() as session:
            from rag_center.modules.indexing.services.legacy_recovery import (
                recover_legacy_index_resources,
            )

            await recover_legacy_index_resources(session, self.settings)
            recovered = await recover_expired_index_jobs(session)
            await repair_queued_deliveries(
                session, redispatch_seconds=self.settings.index_job_redispatch_seconds
            )
            published = await publish_pending_index_outbox(session, publisher=self.publisher)
            if self.container is not None:
                from rag_center.modules.indexing.services.artifacts import cleanup_index_artifacts

                self.cleanup_cursor = await cleanup_index_artifacts(
                    self.container, self.cleanup_cursor
                )
            return published, recovered

    async def run_forever(self) -> None:
        interval = self.settings.index_outbox_interval_seconds
        logger.info("INDEX_OUTBOX_SCHEDULER_STARTED | interval_seconds=%s", interval)
        try:
            while True:
                try:
                    published, recovered = await self.run_once()
                    if published or recovered:
                        logger.info(
                            "INDEX_OUTBOX_SCHEDULER_TICK | published=%s | recovered=%s",
                            published,
                            recovered,
                        )
                except Exception:
                    logger.exception("INDEX_OUTBOX_SCHEDULER_TICK_FAILED")
                await asyncio.sleep(interval)
        finally:
            logger.info("INDEX_OUTBOX_SCHEDULER_STOPPED")


@asynccontextmanager
async def scheduler_resources(settings: Settings) -> AsyncGenerator[None]:
    """保留独立上下文，便于后续接入进程级观测资源。"""

    yield
