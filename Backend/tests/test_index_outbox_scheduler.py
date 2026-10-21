from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from rag_center.core.config import Settings
from rag_center.modules.indexing.models.index_job import IndexOutbox
from rag_center.modules.indexing.services.outbox import (
    publish_pending_index_outbox,
    repair_outbox_for_job,
)


def make_settings() -> Settings:
    return Settings(
        database_url="postgresql+psycopg://rag:rag@localhost/rag_test",
        embedding_api_key="test-key",
        llm_api_key="test-key",
    )


def make_outbox() -> IndexOutbox:
    return IndexOutbox(
        id="outbox-1",
        job_id="job-1",
        topic="index.document",
        payload={"job_id": "job-1"},
        attempts=0,
    )


def mock_session() -> MagicMock:
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_publishes_only_broker_acknowledged_messages() -> None:
    row = make_outbox()
    session = mock_session()
    scalars = MagicMock()
    scalars.all.return_value = [row]
    session.scalars = AsyncMock(return_value=scalars)
    publisher = MagicMock(return_value=MagicMock(id="celery-1"))

    assert await publish_pending_index_outbox(session, publisher=publisher) == 1
    assert row.published_at is not None
    assert row.attempts == 1
    publisher.assert_called_once_with("index.document", {"job_id": "job-1"})


@pytest.mark.asyncio
async def test_failed_publish_keeps_message_pending_and_records_attempt() -> None:
    row = make_outbox()
    session = mock_session()
    scalars = MagicMock()
    scalars.all.return_value = [row]
    session.scalars = AsyncMock(return_value=scalars)
    publisher = MagicMock(side_effect=TimeoutError("broker unavailable"))

    assert await publish_pending_index_outbox(session, publisher=publisher) == 0
    assert row.published_at is None
    assert row.attempts == 1
    assert row.last_error == "TimeoutError"


@pytest.mark.asyncio
async def test_repairs_missing_outbox_row_once() -> None:
    job = MagicMock(job_type="document", id="job-1", status="queued")
    session = mock_session()
    session.scalar = AsyncMock(side_effect=[job, None])
    session.add = MagicMock()

    await repair_outbox_for_job(session, "job-1")

    session.add.assert_called_once()
    session.commit.assert_awaited_once()
