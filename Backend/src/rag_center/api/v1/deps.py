from collections.abc import AsyncIterator

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from rag_center.core.auth import TenantContext, resolve_tenant_context
from rag_center.core.container import get_container


class ApiDependencies:
    """API 依赖集合，便于测试替换和避免全局状态。"""

    @staticmethod
    async def session(request: Request) -> AsyncIterator[AsyncSession]:
        async with get_container(request.app).session_factory() as session:
            yield session

    @staticmethod
    async def tenant(
        request: Request,
        session: AsyncSession = Depends(session),
    ) -> TenantContext:
        return await resolve_tenant_context(
            session,
            request.headers.get("Authorization"),
            get_container(request.app).settings,
        )
