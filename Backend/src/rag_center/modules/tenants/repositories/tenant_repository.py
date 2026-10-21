from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rag_center.modules.tenants.models.tenant import Tenant


class TenantRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, tenant: Tenant) -> Tenant:
        self.db.add(tenant)
        await self.db.flush()
        return tenant

    async def get_by_id(self, tenant_id: str) -> Tenant | None:
        stmt = select(Tenant).where(Tenant.id == tenant_id)
        return await self.db.scalar(stmt)

    async def exists(self, tenant_id: str) -> bool:
        return await self.get_by_id(tenant_id) is not None

    async def update_plan(self, tenant: Tenant, plan: str) -> Tenant:
        tenant.plan = plan
        await self.db.flush()
        return tenant
