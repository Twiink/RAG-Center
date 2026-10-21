from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from rag_center.modules.tenants.models.api_key import ApiKey
from rag_center.modules.tenants.status import ApiKeyStatus, TenantStatus


class ApiKeyRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, api_key: ApiKey) -> ApiKey:
        self.db.add(api_key)
        await self.db.flush()
        return api_key

    async def get_active_by_hash(
        self, key_hash: str, *, now: datetime | None = None
    ) -> ApiKey | None:
        checked_at = now or datetime.now(UTC)
        stmt = (
            select(ApiKey)
            .options(joinedload(ApiKey.tenant))
            .where(
                ApiKey.key_hash == key_hash,
                ApiKey.status == ApiKeyStatus.ACTIVE,
            )
        )
        api_key = await self.db.scalar(stmt)
        if api_key is None:
            return None
        if api_key.expires_at is not None and api_key.expires_at <= checked_at:
            return None
        if api_key.tenant is None or api_key.tenant.status != TenantStatus.ACTIVE:
            return None
        return api_key
