"""OpenTelemetry 链路追踪初始化。

trace_id 通过 get_trace_id() 从当前 OTel span 读取，由 JsonFormatter 自动注入日志。
当 otel_exporter_otlp_endpoint 未配置时，TracerProvider 保持 NoOp，不影响功能。

插桩范围：FastAPI 请求、httpx 外部调用（含 LLM）、SQLAlchemy 查询、Redis 操作、Celery 任务。
每个插桩库均以 try/except 包裹，缺包或版本不兼容不影响启动。
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

if TYPE_CHECKING:
    from rag_center.core.config import Settings

_logger = logging.getLogger("rag_center.tracing")

# 按 (pid, endpoint) 键索引，避免 Celery fork 后覆盖主进程 provider。
_providers: dict[tuple, TracerProvider] = {}
_provider_lock = threading.Lock()

# 已完成插桩的进程 ID；同一进程只插桩一次。
_instrumented: set[int] = set()
_instrumentation_lock = threading.Lock()


@dataclass
class TracingStatus:
    """追踪初始化状态，供 doctor/preflight 展示。"""

    status: str  # "disabled" | "initialized" | "degraded" | "failed"
    instrumentation_failures: tuple[str, ...] = field(default_factory=tuple)


def instrument_libraries() -> tuple[str, ...]:
    """启用 OTel 自动插桩；每个进程只初始化一次，幂等。

    各插桩库缺失或版本不兼容时独立 fallback，不阻断其余插桩。
    """
    pid = os.getpid()
    with _instrumentation_lock:
        if pid in _instrumented:
            return ()
        _instrumented.add(pid)

    failures: list[str] = []

    def _try(name: str, fn) -> None:
        try:
            fn()
        except Exception as exc:
            failures.append(name)
            _logger.warning(
                "OTel 插桩初始化失败",
                extra={"component": name, "error": type(exc).__name__},
            )

    _try(
        "httpx",
        lambda: (
            __import__(
                "opentelemetry.instrumentation.httpx",
                fromlist=["HTTPXClientInstrumentor"],
            )
            .HTTPXClientInstrumentor()
            .instrument()
        ),
    )
    _try(
        "sqlalchemy",
        lambda: (
            __import__(
                "opentelemetry.instrumentation.sqlalchemy",
                fromlist=["SQLAlchemyInstrumentor"],
            )
            .SQLAlchemyInstrumentor()
            .instrument()
        ),
    )
    _try(
        "redis",
        lambda: (
            __import__(
                "opentelemetry.instrumentation.redis",
                fromlist=["RedisInstrumentor"],
            )
            .RedisInstrumentor()
            .instrument()
        ),
    )
    _try(
        "celery",
        lambda: (
            __import__(
                "opentelemetry.instrumentation.celery",
                fromlist=["CeleryInstrumentor"],
            )
            .CeleryInstrumentor()
            .instrument()
        ),
    )

    return tuple(failures)


def setup_tracing(settings: Settings) -> TracingStatus:
    """初始化 OTel TracerProvider 与 OTLP HTTP 导出器。

    进程内幂等——多次调用相同 endpoint 返回同一 provider。
    Celery fork 安全——子进程获得独立 provider，不污染主进程全局状态。

    未配置 otel_exporter_otlp_endpoint 时立即返回 "disabled"，不设置全局 provider。
    """
    if not settings.otel_exporter_otlp_endpoint:
        return TracingStatus(status="disabled")

    failures = instrument_libraries()

    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        identity = (os.getpid(), settings.otel_exporter_otlp_endpoint)
        with _provider_lock:
            if identity not in _providers:
                provider = TracerProvider(
                    resource=Resource.create(
                        {
                            "service.name": "rag-center",
                            "deployment.environment": settings.app_env,
                        }
                    ),
                    sampler=ParentBased(root=TraceIdRatioBased(settings.trace_sample_default_rate)),
                )
                exporter = OTLPSpanExporter(
                    endpoint=settings.otel_exporter_otlp_endpoint,
                    timeout=5,
                )
                provider.add_span_processor(BatchSpanProcessor(exporter))
                _providers[identity] = provider
                # 同一进程第一个 provider 注册为全局 tracer，避免 Celery fork 后覆盖。
                if sum(1 for k in _providers if k[0] == os.getpid()) == 1:
                    trace.set_tracer_provider(provider)

        return TracingStatus(
            status="degraded" if failures else "initialized",
            instrumentation_failures=failures,
        )
    except Exception as exc:
        _logger.warning(
            "OpenTelemetry 初始化失败，应用继续启动",
            extra={"error": type(exc).__name__},
        )
        return TracingStatus(status="failed", instrumentation_failures=failures)


def instrument_fastapi(app) -> None:
    """对已创建的 FastAPI app 实例启用请求级追踪插桩。"""
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor().instrument_app(app)
    except Exception as exc:
        _logger.warning(
            "OTel FastAPI 插桩初始化失败",
            extra={"error": type(exc).__name__},
        )


def get_tracer(name: str = "rag-center") -> trace.Tracer:
    """获取命名 tracer；未初始化时返回 NoOp tracer，不报错。"""
    return trace.get_tracer(name)


def get_trace_id() -> str | None:
    """从当前 OTel span 上下文读取 trace_id；无活跃 span 时返回 None。"""
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx is not None and ctx.is_valid:
        return format(ctx.trace_id, "032x")
    return None
