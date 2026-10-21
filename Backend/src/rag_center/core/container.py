from contextlib import AsyncExitStack
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from rag_center.core.config import Settings
from rag_center.core.metrics import Metrics
from rag_center.infrastructure.db.session import create_db_engine, create_session_factory

if TYPE_CHECKING:
    from rag_center.infrastructure.http.clients import ExternalHttpClients


@dataclass(slots=True)
class ResourceContainer:
    """进程级资源容器，由应用 lifespan 或 Worker 惰性创建。"""

    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    external_clients: "ExternalHttpClients | None" = None
    langfuse: Any | None = None
    langfuse_http_client: httpx.Client | None = None
    redis: Redis | None = None
    metrics: Metrics = field(default_factory=Metrics)

    @classmethod
    def create(cls, settings: Settings) -> "ResourceContainer":
        from rag_center.infrastructure.http.clients import build_external_http_clients
        from rag_center.infrastructure.observability.langfuse_client import build_langfuse_client

        langfuse_http = httpx.Client(trust_env=False) if settings.langfuse_enabled else None
        try:
            langfuse = build_langfuse_client(settings, http_client=langfuse_http)
        except Exception:
            if langfuse_http is not None:
                langfuse_http.close()
            raise
        engine = create_db_engine(settings)
        return cls(
            settings=settings,
            engine=engine,
            session_factory=create_session_factory(engine),
            external_clients=build_external_http_clients(settings),
            redis=Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=3,
                socket_timeout=3,
            ),
            langfuse=langfuse,
            langfuse_http_client=langfuse_http,
        )

    async def aclose(self) -> None:
        import asyncio

        # 任一观测或连接关闭失败，也必须继续释放其余连接池。
        async with AsyncExitStack() as stack:
            stack.push_async_callback(self.engine.dispose)
            if self.external_clients is not None:
                stack.push_async_callback(self.external_clients.aclose)
            if self.langfuse_http_client is not None:
                stack.push_async_callback(asyncio.to_thread, self.langfuse_http_client.close)
            if self.langfuse is not None:
                shutdown = getattr(self.langfuse, "shutdown", None)
                if shutdown is not None:
                    stack.push_async_callback(asyncio.to_thread, shutdown)
                flush = getattr(self.langfuse, "flush", None)
                if flush is not None:
                    stack.push_async_callback(asyncio.to_thread, flush)
            if self.redis is not None:
                stack.push_async_callback(self.redis.aclose)


def get_container(app) -> ResourceContainer:
    container = getattr(app.state, "container", None)
    if container is None:
        raise RuntimeError("Application resources are not initialized")
    return container


active_container: ContextVar[ResourceContainer | None] = ContextVar("container", default=None)
