from __future__ import annotations

from dataclasses import dataclass

import httpx
from fastapi import Request

from rag_center.core.config import Settings
from rag_center.core.container import get_container


@dataclass(slots=True)
class ExternalHttpClients:
    model: httpx.AsyncClient
    elasticsearch: httpx.AsyncClient

    async def aclose(self) -> None:
        import asyncio

        await asyncio.gather(
            self.model.aclose(),
            self.elasticsearch.aclose(),
        )


def build_external_http_clients(settings: Settings) -> ExternalHttpClients:
    limits = httpx.Limits(
        max_connections=settings.external_http_max_connections,
        max_keepalive_connections=settings.external_http_max_keepalive_connections,
        keepalive_expiry=settings.external_http_keepalive_expiry_seconds,
    )
    return ExternalHttpClients(
        model=httpx.AsyncClient(limits=limits),
        elasticsearch=httpx.AsyncClient(limits=limits, trust_env=False),
    )


def get_external_http_clients(request: Request) -> ExternalHttpClients:
    container = get_container(request.app)
    if container.external_clients is None:
        raise RuntimeError("external HTTP clients are not initialized")
    return container.external_clients
