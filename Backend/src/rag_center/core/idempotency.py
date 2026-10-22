"""请求幂等性管理：基于客户端提供的幂等键，确保相同请求不会重复执行。

所有 Redis 操作在失败时放行（open 模式），不阻断主流程。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

# 幂等键 TTL：24 小时
IDEMPOTENCY_TTL = 86400

_logger = logging.getLogger("rag_center.idempotency")


class IdempotencyManager:
    """幂等性管理器；基于幂等键存储请求结果，防止重复执行。

    所有 Redis 操作均以 try/except 包裹，Redis 故障时放行，不阻断服务。
    """

    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    def _key(self, tenant_id: str, idempotency_key: str) -> str:
        """生成 Redis 键：租户隔离的幂等键。"""
        return f"rag:idempotency:{tenant_id}:{idempotency_key}"

    async def get(self, tenant_id: str, idempotency_key: str) -> dict | None:
        """获取已存在的请求结果；未找到返回 None。"""
        try:
            cached = await self.redis.get(self._key(tenant_id, idempotency_key))
            if cached:
                return json.loads(cached)
        except (RedisError, json.JSONDecodeError) as exc:
            _logger.warning(
                "幂等性检查失败",
                extra={"error": type(exc).__name__, "tenant_id": tenant_id},
            )
        return None

    async def set(
        self,
        tenant_id: str,
        idempotency_key: str,
        result: Any,
        ttl: int = IDEMPOTENCY_TTL,
    ) -> None:
        """保存请求结果；用于幂等性保护。"""
        try:
            await self.redis.setex(
                self._key(tenant_id, idempotency_key),
                ttl,
                json.dumps(result),
            )
        except (RedisError, TypeError) as exc:
            _logger.warning(
                "幂等性保存失败",
                extra={"error": type(exc).__name__, "tenant_id": tenant_id},
            )

    async def lock(self, tenant_id: str, idempotency_key: str, ttl: int = 300) -> bool:
        """尝试获取幂等锁；返回 True 表示获取成功或 Redis 故障放行，False 表示已有相同请求在处理。"""
        try:
            acquired = await self.redis.set(
                self._key(tenant_id, idempotency_key) + ":lock",
                "1",
                ex=ttl,
                nx=True,
            )
            return acquired is not None
        except RedisError as exc:
            _logger.warning(
                "幂等锁获取失败，放行请求",
                extra={"error": type(exc).__name__, "tenant_id": tenant_id},
            )
            return True

    async def unlock(self, tenant_id: str, idempotency_key: str) -> None:
        """释放幂等锁；请求完成后调用，依赖 TTL 自动到期兜底。"""
        try:
            await self.redis.delete(self._key(tenant_id, idempotency_key) + ":lock")
        except RedisError as exc:
            _logger.warning(
                "幂等锁释放失败",
                extra={"error": type(exc).__name__, "tenant_id": tenant_id},
            )


def get_idempotency_manager() -> IdempotencyManager:
    """获取当前容器的幂等性管理器实例。"""
    from rag_center.core.container import active_container

    container = active_container.get()
    if container is None or container.redis is None:
        raise RuntimeError("幂等性管理需要已初始化的资源容器")
    return IdempotencyManager(container.redis)
