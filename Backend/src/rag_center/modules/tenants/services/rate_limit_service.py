from __future__ import annotations

from datetime import UTC, datetime, timedelta
from time import time

import redis.asyncio as redis

from rag_center.core.exceptions import AppError, ErrorCode


class RateLimitService:
    def __init__(self, redis_client: redis.Redis) -> None:
        self.redis = redis_client

    async def check_retrieve(self, tenant_id: str, *, qps_limit: int, daily_limit: int) -> None:
        await self._check_qps(tenant_id, qps_limit)
        await self._check_daily_quota(tenant_id, daily_limit)

    async def record_retrieve_success(self, tenant_id: str) -> None:
        daily_key = _daily_retrieve_key(tenant_id)
        pipe = self.redis.pipeline()
        pipe.incr(daily_key)
        pipe.expire(daily_key, _seconds_until_end_of_utc_day() + 3600)
        await pipe.execute()

    async def get_daily_retrieve_count(self, tenant_id: str) -> int:
        return int(await self.redis.get(_daily_retrieve_key(tenant_id)) or 0)

    async def _check_qps(self, tenant_id: str, qps_limit: int) -> None:
        second = int(time())
        key = f"rag:ratelimit:retrieve:{tenant_id}:{second}"
        count = int(await self.redis.incr(key))
        if count == 1:
            await self.redis.expire(key, 2)
        if count > qps_limit:
            raise AppError(
                ErrorCode.API_RATE_LIMIT,
                msg="检索请求过于频繁，请稍后再试",
                context={"tenant_id": tenant_id, "qps_limit": qps_limit},
            )

    async def _check_daily_quota(self, tenant_id: str, daily_limit: int) -> None:
        daily_key = _daily_retrieve_key(tenant_id)
        count = int(await self.redis.get(daily_key) or 0)
        if count >= daily_limit:
            raise AppError(
                ErrorCode.QUOTA_EXCEEDED,
                msg="日检索量已达上限",
                context={"tenant_id": tenant_id, "daily_limit": daily_limit},
            )


def _daily_retrieve_key(tenant_id: str) -> str:
    day = datetime.now(UTC).strftime("%Y%m%d")
    return f"rag:quota:retrieve:daily:{tenant_id}:{day}"


def _seconds_until_end_of_utc_day() -> int:
    now = datetime.now(UTC)
    end = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(int((end - now).total_seconds()), 1)


def get_rate_limit_service() -> RateLimitService:
    from rag_center.core.container import active_container

    container = active_container.get()
    if container is None or container.redis is None:
        raise RuntimeError("限流服务需要已初始化的资源容器")
    return RateLimitService(container.redis)
