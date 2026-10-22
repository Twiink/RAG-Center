"""月度 LLM 成本配额管理：按租户跟踪 LLM 调用费用，超出月度预算时拒绝请求。

使用 Redis Hash 存储每月累计成本，Lua 脚本保证记账幂等性。
Redis 故障时放行，不阻断服务（open 模式）。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from rag_center.core.exceptions import AppError, ErrorCode

if TYPE_CHECKING:
    from rag_center.core.config import Settings

# 按 call_id 幂等记账：同一 call_id 不重复累加。
_RECORD_SCRIPT = """
local key = KEYS[1]
local cost = tonumber(ARGV[1])
local call_id = ARGV[2]
if redis.call('HEXISTS', key, call_id) == 1 then
    return tonumber(redis.call('HGET', key, 'total') or '0')
end
local new_total = (tonumber(redis.call('HGET', key, 'total') or '0')) + cost
redis.call('HSET', key, 'total', new_total, call_id, '1')
redis.call('EXPIRE', key, 2592000)
return new_total
"""

# Redis 键 TTL：31 天（月度数据保留至下月初后自动过期）
_KEY_TTL = 2_592_000

_logger = logging.getLogger("rag_center.llm_cost")


def _current_month() -> str:
    return datetime.now(UTC).strftime("%Y%m")


class LLMCostManager:
    """月度 LLM 成本配额管理器。

    所有 Redis 操作在失败时放行，确保配额服务不成为单点故障。
    """

    def __init__(self, redis: Redis, logger: Any, settings: Settings) -> None:
        self.redis = redis
        self.logger = logger
        self.settings = settings

    def _key(self, tenant_id: str) -> str:
        """生成当月的 Redis Hash 键。"""
        return f"rag:llm:cost:{tenant_id}:{_current_month()}"

    async def check(self, tenant_id: str) -> None:
        """检查租户月度 LLM 成本是否超出预算；超出时抛出 QUOTA_EXCEEDED 异常。"""
        budget = self.settings.llm_monthly_cost_budget_usd
        if budget is None:
            return

        try:
            raw = await self.redis.hget(self._key(tenant_id), "total")  # type: ignore
            used = float(raw) if raw else 0.0
            if used >= budget:
                raise AppError(
                    ErrorCode.QUOTA_EXCEEDED,
                    msg=f"本月 LLM 费用配额已用尽（${used:.4f}/${budget:.2f}）",
                    context={"tenant_id": tenant_id, "used_usd": used, "budget_usd": budget},
                )
        except AppError:
            raise
        except RedisError as exc:
            _logger.warning(
                "LLM 成本配额后端不可用，放行请求",
                extra={"error": type(exc).__name__, "tenant_id": tenant_id},
            )

    async def record(self, tenant_id: str, cost_usd: float, call_id: str) -> None:
        """按 call_id 幂等记录 LLM 调用成本；重试重入不会重复计费。"""
        if cost_usd <= 0:
            return
        if self.settings.llm_monthly_cost_budget_usd is None:
            return

        try:
            await self.redis.eval(  # type: ignore
                _RECORD_SCRIPT, 1, self._key(tenant_id), str(cost_usd), call_id
            )
        except RedisError as exc:
            _logger.warning(
                "LLM 成本记录失败",
                extra={"error": type(exc).__name__, "tenant_id": tenant_id, "cost_usd": cost_usd},
            )

    async def get_usage(self, tenant_id: str) -> dict:
        """查询当月 LLM 成本使用量。"""
        budget = self.settings.llm_monthly_cost_budget_usd
        month = _current_month()
        try:
            raw = await self.redis.hget(self._key(tenant_id), "total")  # type: ignore
            used = float(raw) if raw else 0.0
            result: dict = {"month": month, "used_usd": used}
            if budget is not None:
                result["budget_usd"] = budget
                result["remaining_usd"] = max(0.0, budget - used)
            return result
        except RedisError as exc:
            return {"month": month, "error": type(exc).__name__}


def get_llm_cost_manager() -> LLMCostManager:
    """获取当前容器的 LLM 成本管理器实例。"""
    from rag_center.core.container import active_container
    from rag_center.core.logging import get_logger

    container = active_container.get()
    if container is None or container.redis is None:
        raise RuntimeError("LLM 成本管理需要已初始化的资源容器")
    return LLMCostManager(container.redis, get_logger("rag_center.llm_cost"), container.settings)
