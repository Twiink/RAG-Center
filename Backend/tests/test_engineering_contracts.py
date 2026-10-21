from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from rag_center.core.config import (
    Settings,
    get_settings,
    load_settings,
    redacted_settings,
    settings_context,
)
from rag_center.core.logging import (
    JsonFormatter,
    RequestIdFilter,
    job_id_var,
    reset_request_id,
    set_request_id,
)
from rag_center.main import create_app
from rag_center.modules.indexing.models.index_job import IndexJob
from rag_center.modules.indexing.services.job_executor import claim_index_job, fail_index_job
from rag_center.schemas.common import ApiErrorResponse, ApiResponse, success_response


def test_config_sources_obey_precedence_and_grouping(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config/environments").mkdir(parents=True)
    (tmp_path / "config/environments/test.yaml").write_text("database:\n  pool_size: 7\n")
    (tmp_path / "config/private.yaml").write_text("database:\n  pool_size: 8\n")
    (tmp_path / ".env").write_text("RAG_DATABASE__POOL_SIZE=9\nRAG_APP_ENV=test\n")
    assert load_settings().database_pool_size == 9
    monkeypatch.setenv("RAG_DATABASE__POOL_SIZE", "10")
    settings = load_settings({"database": {"pool_size": 11}})
    assert settings.database_pool_size == 11
    assert settings.app_env == "test"
    assert redacted_settings(settings)["sources"]["database_pool_size"] == "overrides"
    assert load_settings().database_pool_size == 10


def test_configuration_secrets_are_redacted():
    settings = Settings(
        database_url="postgresql+psycopg://someone:hidden@db/rag",
        model_api_key="test-secret",
        redis_url="redis://:hidden@cache/0",
    )
    displayed = str(redacted_settings(settings))
    assert "hidden" not in displayed
    assert "test-secret" not in displayed


def test_configuration_cross_validation_and_context_isolation():
    with pytest.raises(ValidationError):
        Settings(chunk_overlap=900, chunk_size=800)
    with pytest.raises(ValidationError):
        Settings(embedding_dimensions=10)
    with pytest.raises(ValidationError):
        Settings(index_job_max_attempts=4)
    first, second = Settings(top_k=3), Settings(top_k=7)
    with settings_context(first):
        assert get_settings().top_k == 3
        with settings_context(second):
            assert get_settings().top_k == 7
        assert get_settings().top_k == 3


def test_response_request_id_and_health_do_not_need_database(tmp_path):
    app = create_app(Settings(log_dir=str(tmp_path), elasticsearch_enabled=False))

    @app.get("/contract", response_model=ApiResponse[dict[str, bool]])
    async def contract():
        return success_response({"ok": True})

    with TestClient(app) as client:
        response = client.get("/contract", headers={"X-Request-ID": "request-test"})
        assert response.json()["request_id"] == response.headers["X-Request-ID"] == "request-test"
        assert client.get("/health/live").status_code == 200
        assert client.get("/does-not-exist").json()["code"] == 20006
        unauthorized = client.get("/api/v1/auth/me")
        assert unauthorized.status_code == 401
        assert ApiErrorResponse.model_validate(unauthorized.json()).request_id
        metrics = client.get("/metrics")
        assert "rag_http_requests_total" in metrics.text
        assert "/contract" in metrics.text
    schema = app.openapi()
    responses = schema["paths"]["/api/v1/knowledge-bases"]["post"]["responses"]
    for status in ("401", "409", "422", "500"):
        assert responses[status]["content"]["application/json"]["schema"]["$ref"].endswith(
            "/ApiErrorResponse"
        )
    assert "HTTPValidationError" not in schema["components"]["schemas"]


@pytest.mark.asyncio
async def test_duplicate_message_cannot_bypass_retry_backoff():
    session = MagicMock(rollback=AsyncMock(), scalar=AsyncMock(), commit=AsyncMock())
    job = IndexJob(
        id="job",
        status="queued",
        attempt=1,
        max_attempts=3,
        available_at=datetime.now(UTC) + timedelta(hours=1),
    )
    session.scalar.return_value = job
    assert await claim_index_job(session, "job", Settings()) is None
    assert job.attempt == 1
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_first_attempt_permanent_failure_is_terminal_and_no_outbox():
    job = IndexJob(
        id="job",
        resource_id="document",
        tenant_id="tenant",
        job_type="document",
        status="running",
        lease_token="token",
        attempt=1,
        max_attempts=3,
    )
    session = MagicMock(scalar=AsyncMock(return_value=job), commit=AsyncMock(), execute=AsyncMock())
    await fail_index_job(
        session,
        job_id="job",
        lease_token="token",
        error_message="empty",
        retry_backoff_seconds=5,
        permanent=True,
    )
    assert job.status == "failed"
    session.add.assert_not_called()


def test_json_log_keeps_request_and_job_context():
    import json
    import logging

    token = set_request_id("request")
    task_token = job_id_var.set("job")
    try:
        record = logging.LogRecord("test", logging.INFO, "", 0, "hello %s", ("world",), None)
        RequestIdFilter().filter(record)
        data = json.loads(JsonFormatter().format(record))
        assert data["request_id"] == "request" and data["job_id"] == "job"
    finally:
        reset_request_id(token)
        job_id_var.reset(task_token)


@pytest.mark.asyncio
async def test_container_releases_pools_when_observability_shutdown_fails():
    from types import SimpleNamespace

    from rag_center.core.container import ResourceContainer

    engine = SimpleNamespace(dispose=AsyncMock())
    redis = SimpleNamespace(aclose=AsyncMock())
    http = SimpleNamespace(aclose=AsyncMock())
    observation = SimpleNamespace(
        flush=MagicMock(side_effect=RuntimeError("offline")), shutdown=MagicMock()
    )
    container = ResourceContainer(
        settings=Settings(),
        engine=engine,
        session_factory=MagicMock(),
        redis=redis,
        external_clients=http,
        langfuse=observation,
    )
    with pytest.raises(RuntimeError, match="offline"):
        await container.aclose()
    engine.dispose.assert_awaited_once()
    redis.aclose.assert_awaited_once()
    http.aclose.assert_awaited_once()
    observation.shutdown.assert_called_once()
