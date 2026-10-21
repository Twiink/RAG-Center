from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from rag_center.core.config import Settings
from rag_center.core.exceptions import AppError, ErrorCode
from rag_center.modules.tenants.api_key_crypto import hash_api_key
from rag_center.modules.tenants.repositories.api_key_repository import ApiKeyRepository
from rag_center.modules.tenants.repositories.tenant_repository import TenantRepository

if TYPE_CHECKING:
    pass

DEFAULT_DEV_TENANT_ID = "tenant_demo"


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    tenant_name: str
    key_id: str | None
    key_prefix: str | None
    key_name: str | None


def parse_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token or None


async def resolve_tenant_context(
    db: AsyncSession,
    authorization: str | None,
    settings: Settings,
) -> TenantContext:
    if not settings.auth_enabled:
        tenant_repository = TenantRepository(db)
        tenant = await tenant_repository.get_by_id(DEFAULT_DEV_TENANT_ID)
        tenant_name = tenant.name if tenant is not None else DEFAULT_DEV_TENANT_ID
        return TenantContext(
            tenant_id=DEFAULT_DEV_TENANT_ID,
            tenant_name=tenant_name,
            key_id=None,
            key_prefix=None,
            key_name=None,
        )

    raw_key = parse_bearer_token(authorization)
    if raw_key is None:
        raise AppError(ErrorCode.UNAUTHORIZED)

    api_key_repository = ApiKeyRepository(db)
    api_key = await api_key_repository.get_active_by_hash(hash_api_key(raw_key))
    if api_key is None or api_key.tenant is None:
        raise AppError(ErrorCode.UNAUTHORIZED)

    return TenantContext(
        tenant_id=api_key.tenant_id,
        tenant_name=api_key.tenant.name,
        key_id=api_key.id,
        key_prefix=api_key.key_prefix,
        key_name=api_key.name,
    )
