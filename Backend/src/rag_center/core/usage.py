"""LLM 使用量追踪与成本估算"""

from contextvars import ContextVar
from typing import Any

# 价格表（美元/千 token）
PRICING_TABLE: dict[tuple[str, str], tuple[float, float]] = {
    # provider, model -> (prompt_price, completion_price)
    ("openai", "gpt-4o"): (0.0025, 0.01),
    ("openai", "gpt-4o-mini"): (0.00015, 0.0006),
    ("openai", "gpt-3.5-turbo"): (0.0005, 0.0015),
    ("openai", "text-embedding-3-small"): (0.00002, 0.00002),
    ("openai", "text-embedding-3-large"): (0.00013, 0.00013),
    ("anthropic", "claude-3-5-sonnet-20241022"): (0.003, 0.015),
    ("anthropic", "claude-3-5-haiku-20241022"): (0.001, 0.005),
}


def estimate_cost(provider: str, model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """估算 LLM 调用成本（美元）"""
    key = (provider.lower(), model.lower())
    if key not in PRICING_TABLE:
        return 0.0

    prompt_price, completion_price = PRICING_TABLE[key]
    return (prompt_tokens / 1000 * prompt_price) + (completion_tokens / 1000 * completion_price)


class UsageAccumulator:
    """线程安全的使用量累加器"""

    def __init__(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.cost = 0.0

    def add(self, provider: str, model: str, prompt_tokens: int, completion_tokens: int) -> None:
        """累加一次 LLM 调用的使用量"""
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.total_tokens += prompt_tokens + completion_tokens
        self.cost += estimate_cost(provider, model, prompt_tokens, completion_tokens)

    def to_dict(self) -> dict[str, Any]:
        """导出为字典"""
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost, 6),
        }


# 当前请求的使用量累加器
usage_accumulator_var: ContextVar[UsageAccumulator | None] = ContextVar(
    "usage_accumulator", default=None
)


def get_usage_accumulator() -> UsageAccumulator:
    """获取或创建当前上下文的使用量累加器"""
    acc = usage_accumulator_var.get()
    if acc is None:
        acc = UsageAccumulator()
        usage_accumulator_var.set(acc)
    return acc
