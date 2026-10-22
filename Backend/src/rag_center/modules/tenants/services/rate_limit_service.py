"""检索请求限流：滑动窗口 QPS + 日配额；Redis 故障按故障策略放行或拒绝。

设计说明：
- QPS 限制使用 ZADD 滑动窗口，消除固定窗口在秒边界的 2× 突刺。
- 日配额使用 INCR，在 UTC 日结束时自动过期。
- Redis 不可用时按 ``settings.rate_limit_failure_mode`` 处理：
    open（默认）：放行并记录告警；
    closed：返回 503 并记录告警。
- 限流命中和后端故障均通过 ``container.metrics.ratelimit_rejections`` 计数。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from time import time
from typing import TYPE_CHECKING

from redis.exceptions import RedisError

from rag_center.core.exceptions import AppError, ErrorCode

if TYPE_CHECKING:
    import redis.asyncio as redis

    from rag_center.core.config import Settings
    from rag_center.core.metrics import Metrics

_logger = logging.getLogger("rag_center.rate_limit")

# 滑动窗口 Lua 脚本：原子检查+记录，避免竞态条件。
# KEYS[1] = 有序集合键；ARGV[1] = 当前时间戳（ms）；ARGV[2] = 窗口大小（ms）；ARGV[3] = 上限
_SLIDING_WINDOW_SCRIPT = """
local key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', key, '-inf', now_ms - window_ms)
local count = tonumber(redis.call('ZCARD', key))
if count >= limit then
    redis.call('EXPIRE', key, math.ceil(window_ms / 1000) + 1)
    return -1
end
redis.call('ZADD', key, now_ms, now_ms .. ':' .. math.random(1000000))
redis.call('EXPIRE', key, math.ceil(window_ms / 1000) + 1)
return count + 1
"""


class RateLimitService:
    def __init__(
        self,
        redis_client: redis.Redis,
        settings: Settings,
        metrics: Metrics | None = None,
    ) -> None:
        self.redis = redis_client
        self.settings = settings
        self.metrics = metrics

    async def check_retrieve(self, tenant_id: str, *, qps_limit: int, daily_limit: int) -> None:
        """检查 QPS 滑动窗口和日配额；超限或 closed 模式下抛 AppError。"""
        await self._check_sliding_window(tenant_id, limit=qps_limit, window_seconds=1)
        await self._check_daily_quota(tenant_id, daily_limit)

    async def record_retrieve_success(self, tenant_id: str) -> None:
        """请求成功后递增日配额计数器；Redis 失败时静默跳过。"""
        daily_key = _daily_retrieve_key(tenant_id)
        try:
            pipe = self.redis.pipeline()
            pipe.incr(daily_key)
            pipe.expire(daily_key, _seconds_until_end_of_utc_day() + 3600)
            await pipe.execute()
        except (RedisError, OSError):
            _logger.warning("日配额记录失败，已跳过", extra={"tenant_id": tenant_id})

    async def get_daily_retrieve_count(self, tenant_id: str) -> int:
        try:
            return int(await self.redis.get(_daily_retrieve_key(tenant_id)) or 0)
        except (RedisError, OSError):
            return 0

    async def _check_sliding_window(
        self, tenant_id: str, *, limit: int, window_seconds: int
    ) -> None:
        key = f"rag:ratelimit:sw:{tenant_id}:{window_seconds}s"
        now_ms = int(time() * 1000)
        window_ms = window_seconds * 1000
        try:
            result = int(
                await self.redis.eval(  # type: ignore
                    _SLIDING_WINDOW_SCRIPT, 1, key, str(now_ms), str(window_ms), str(limit)
                )
            )
        except (RedisError, OSError) as exc:
            _logger.warning(
                "限流后端不可用",
                extra={
                    "failure_mode": self.settings.rate_limit_failure_mode,
                    "error": type(exc).__name__,
                },
            )
            if self.settings.rate_limit_failure_mode == "closed":
                if self.metrics:
                    self.metrics.ratelimit_rejections.labels(kind="backend_error").inc()
                raise AppError(
                    ErrorCode.SYSTEM_BUSY,
                    msg="限流服务暂不可用",
                ) from exc
            return
        if result == -1:
            if self.metrics:
                self.metrics.ratelimit_rejections.labels(kind="qps").inc()
            raise AppError(
                ErrorCode.API_RATE_LIMIT,
                msg="检索请求过于频繁，请稍后再试",
                context={"tenant_id": tenant_id, "qps_limit": limit},
            )

    async def _check_daily_quota(self, tenant_id: str, daily_limit: int) -> None:
        daily_key = _daily_retrieve_key(tenant_id)
        try:
            count = int(await self.redis.get(daily_key) or 0)
        except (RedisError, OSError):
            _logger.warning("日配额检查失败，已放行", extra={"tenant_id": tenant_id})
            return
        if count >= daily_limit:
            if self.metrics:
                self.metrics.ratelimit_rejections.labels(kind="daily_quota").inc()
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
    return RateLimitService(container.redis, container.settings, container.metrics)
