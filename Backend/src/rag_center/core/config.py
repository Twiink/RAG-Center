from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values
from pydantic import Field, PrivateAttr, SecretStr, TypeAdapter, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = (
    Path.cwd() / "Backend" if (Path.cwd() / "Backend/pyproject.toml").exists() else Path.cwd()
)
CONFIG_ROOT = PROJECT_ROOT / "config"
ENV_FILE = PROJECT_ROOT / ".env"
_yaml_values: ContextVar[dict[str, Any] | None] = ContextVar("yaml_values", default=None)
_active_settings: ContextVar[Settings | None] = ContextVar("settings", default=None)
VECTOR_DIMENSIONS = 1536


class Settings(BaseSettings):
    """运行配置。业务模块通过注入读取，不直接依赖全局状态。"""

    app_env: str = "local"
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/rag_center"
    database_pool_size: int = Field(default=10, ge=1)
    database_max_overflow: int = Field(default=20, ge=0)

    model_base_url: str = "https://api.openai.com/v1"
    model_api_key: SecretStr = SecretStr("")
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    embedding_batch_size: int = Field(default=10, ge=1, le=100)

    llm_provider: str = "openai_compatible"
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = "deepseek-v4-flash"
    llm_strong_model: str = "deepseek-v4-pro"
    llm_timeout_seconds: int = Field(default=60, ge=1)

    vector_store: str = "pgvector"
    keyword_search_provider: str = "elasticsearch"
    elasticsearch_enabled: bool = True
    elasticsearch_url: str = "http://localhost:9200"
    elasticsearch_index: str = "rag_chunks"
    elasticsearch_timeout_seconds: int = Field(default=30, ge=1)

    external_http_max_connections: int = Field(default=100, ge=1)
    external_http_max_keepalive_connections: int = Field(default=20, ge=0)
    external_http_keepalive_expiry_seconds: float = Field(default=30.0, ge=0)
    external_http_timeout_seconds: float = Field(default=60.0, gt=0)

    retrieval_mode: str = "vector"
    hybrid_fusion: str = "rrf"
    hybrid_rrf_k: int = Field(default=60, ge=1)
    hybrid_vector_top_k: int = Field(default=20, ge=1, le=50)
    hybrid_bm25_top_k: int = Field(default=20, ge=1, le=50)
    hybrid_top_n: int = Field(default=20, ge=1, le=50)

    chunk_size: int = Field(default=800, ge=100)
    chunk_overlap: int = Field(default=100, ge=0)
    table_max_rows_per_chunk: int = Field(default=10, ge=1, le=100)
    top_k: int = Field(default=5, ge=1, le=50)
    multi_kb_max: int = Field(default=5, ge=1, le=10)
    query_max_length: int = Field(default=2000, ge=1, le=10000)

    rerank_enabled: bool = False
    rerank_base_url: str = ""
    rerank_api_key: SecretStr = SecretStr("")
    rerank_model: str = "qwen3-rerank"
    rerank_timeout_seconds: int = Field(default=15, ge=1)

    evidence_enabled: bool = False
    evidence_timeout_seconds: int = Field(default=15, ge=1)

    log_level: str = "INFO"
    log_dir: str = "logs"
    log_file_enabled: bool = True
    log_request_body: bool = False
    log_response_body: bool = False
    log_json: bool = True
    log_body_max_chars: int = 4000
    log_llm_max_chars: int = 8000
    log_model_content: bool = False
    parser_max_concurrency: int = Field(default=4, ge=1)

    # 指标配置
    metrics_enabled: bool = True
    metrics_port: int = 9090

    # LLM 重试配置
    llm_max_retries: int = 2
    llm_retry_delay_seconds: float = 1.0

    log_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    log_backup_count: int = Field(default=5, ge=1)
    log_error_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    log_error_backup_count: int = Field(default=5, ge=1)
    log_daily_backup_count: int = Field(default=7, ge=1)

    auth_enabled: bool = True
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # 限流故障策略：open = Redis 故障时放行，closed = 返回 503
    rate_limit_failure_mode: str = "open"
    # LLM 月度成本预算（美元/租户）；None 表示不限制
    llm_monthly_cost_budget_usd: float | None = None
    # 幂等响应缓存 TTL（秒）
    idempotency_ttl_seconds: int = 86400

    query_rewrite_enabled: bool = False
    query_rewrite_timeout_ms: int = Field(default=2000, ge=100)

    langfuse_enabled: bool = False
    langfuse_host: str = "http://localhost:3000"
    langfuse_public_key: SecretStr = SecretStr("")
    langfuse_secret_key: SecretStr = SecretStr("")

    document_storage_path: str = "data/uploads"
    document_max_size_mb: int = Field(default=20, ge=1, le=200)

    index_job_max_attempts: int = Field(default=3, ge=1, le=3)
    index_job_lease_seconds: int = Field(default=300, ge=1)
    index_job_retry_backoff_seconds: int = Field(default=5, ge=1)
    index_job_redispatch_seconds: int = Field(default=60, ge=1)
    index_outbox_interval_seconds: float = Field(default=1.0, gt=0)

    # OTel 追踪配置
    otel_exporter_otlp_endpoint: str = ""
    trace_sample_default_rate: float = 0.001  # 0.1%
    trace_sample_slow_rate: float = 0.10  # 10%
    trace_sample_p99_threshold_ms: float = 5000.0

    # Research 配置
    research_stage_timeout_seconds: int = 300
    research_strict_mode: bool = False

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        env_prefix="RAG_",
        extra="ignore",
    )

    _field_sources: dict[str, str] = PrivateAttr(default_factory=dict)

    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        def env_source():
            return _environment_values(env_settings.env_vars)

        def dotenv_source():
            return _environment_values(dotenv_settings.env_vars)

        return init_settings, env_source, dotenv_source, lambda: _yaml_values.get() or {}

    @model_validator(mode="after")
    def validate_relationships(self):
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap 必须小于 chunk_size")
        if self.embedding_dimensions != VECTOR_DIMENSIONS:
            raise ValueError(f"embedding_dimensions 必须与迁移定义一致：{VECTOR_DIMENSIONS}")
        if self.external_http_max_keepalive_connections > self.external_http_max_connections:
            raise ValueError("HTTP keepalive 连接数不能超过最大连接数")
        if self.langfuse_enabled and (
            not self.langfuse_public_key.get_secret_value()
            or not self.langfuse_secret_key.get_secret_value()
        ):
            raise ValueError("启用 Langfuse 必须配置 public_key 和 secret_key")
        return self

    @field_validator("database_url")
    @classmethod
    def require_async_driver(cls, value: str) -> str:
        if not value.startswith("postgresql+psycopg://"):
            raise ValueError("database_url 必须使用 postgresql+psycopg:// 异步驱动")
        return value


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    adapter = TypeAdapter(dict[str, Any])
    return adapter.validate_python(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def _flatten(values: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    result = {}
    for key, value in values.items():
        name = f"{prefix}_{key}" if prefix else key
        if isinstance(value, dict):
            result.update(_flatten(value, name))
        else:
            result[name] = value
    return result


def _environment_values(values) -> dict[str, Any]:
    # 分组环境变量 RAG_DATABASE__URL 与字段 database_url 对齐。
    result = {}
    for key, value in values.items():
        key = key.lower()
        if key.startswith("rag_"):
            name = key[4:].replace("__", "_")
            if name in Settings.model_fields and value is not None:
                result[name] = value
    return result


def load_settings(*overrides: Any, env: str | None = None) -> Settings:
    """默认值 < 包内 YAML < 环境 YAML < 私有 YAML < .env < 环境变量 < 显式覆盖。"""
    explicit = {}
    for item in overrides:
        if item:
            explicit.update(item.model_dump() if isinstance(item, Settings) else _flatten(item))
    workspace = (
        Path.cwd() / "Backend" if (Path.cwd() / "Backend/pyproject.toml").exists() else Path.cwd()
    )
    env_file = workspace / ".env"
    dotenv = _environment_values(dotenv_values(env_file)) if env_file.exists() else {}
    environ = _environment_values(os.environ)
    active_env = (
        env or explicit.get("app_env") or environ.get("app_env") or dotenv.get("app_env") or "local"
    )
    if active_env not in {"local", "test", "production"}:
        raise ValueError("app_env 必须是 local、test 或 production")
    config_root = Path(os.environ.get("RAG_CONFIG_DIR", workspace / "config"))
    base_text = files("rag_center").joinpath("config/base.yaml").read_text(encoding="utf-8")
    sources = [
        ("package:base.yaml", _flatten(yaml.safe_load(base_text) or {})),
        (
            f"environment:{active_env}",
            _flatten(_read_yaml(config_root / f"environments/{active_env}.yaml")),
        ),
        ("private.yaml", _flatten(_read_yaml(config_root / "private.yaml"))),
    ]
    merged = {"app_env": active_env}
    field_sources = {key: "defaults" for key in Settings.model_fields}
    for source, values in sources:
        unknown = values.keys() - Settings.model_fields.keys()
        if unknown:
            raise ValueError(f"配置包含未知字段：{sorted(unknown)}")
        merged.update(values)
        field_sources.update(dict.fromkeys(values, source))
    merged["app_env"] = active_env
    token = _yaml_values.set(merged)
    try:
        settings = Settings(_env_file=env_file, **explicit)
    finally:
        _yaml_values.reset(token)
    for source, values in [(".env", dotenv), ("environment", environ), ("overrides", explicit)]:
        field_sources.update(dict.fromkeys(values, source))
    settings._field_sources = field_sources
    return settings


@lru_cache
def _default_settings() -> Settings:
    return load_settings()


def get_settings() -> Settings:
    return _active_settings.get() or _default_settings()


# 保留测试和 CLI 中清理默认配置缓存的入口。
get_settings.cache_clear = _default_settings.cache_clear  # ty: ignore[unresolved-attribute]


@contextmanager
def settings_context(settings: Settings):
    """为请求或任务绑定注入配置，退出时恢复上一上下文。"""
    token = _active_settings.set(settings)
    try:
        yield
    finally:
        _active_settings.reset(token)


def redacted_settings(settings: Settings) -> dict[str, Any]:
    values = settings.model_dump(mode="json")
    for key, value in settings:
        if isinstance(value, SecretStr):
            values[key] = "******" if value.get_secret_value() else ""
        elif key.endswith("url") and isinstance(value, str):
            from urllib.parse import urlsplit, urlunsplit

            parsed = urlsplit(value)
            host = parsed.netloc.rsplit("@", 1)[-1]
            values[key] = urlunsplit(
                (
                    parsed.scheme,
                    f"***@{host}" if "@" in parsed.netloc else host,
                    parsed.path,
                    "",
                    "",
                )
            )
    return {"sources": settings._field_sources, "values": values}
