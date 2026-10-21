"""LLM 调用韧性辅助工具：重试、指标、错误分类。"""

import asyncio
from collections.abc import Callable
from typing import Any, TypeVar

import httpx

from rag_center.core.exceptions import ErrorCode
from rag_center.core.logging import get_logger
from rag_center.infrastructure.providers.llm.base import LLMProviderError

T = TypeVar("T")

logger = get_logger(__name__)


def classify_llm_error(exc: Exception) -> tuple[ErrorCode, bool]:
    """
    分类 LLM 错误并返回 (ErrorCode, retryable)。

    Args:
        exc: 异常实例

    Returns:
        (错误码, 是否可重试)
    """
    if isinstance(exc, httpx.TimeoutException):
        return ErrorCode.LLM_TIMEOUT, True

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 429:
            return ErrorCode.LLM_RATE_LIMIT, True
        if 500 <= status < 600:
            return ErrorCode.LLM_PROVIDER_FAILED, True
        if status == 400:
            # 400 可能是 token 超限，不可重试
            body = exc.response.text
            if "token" in body.lower() or "length" in body.lower():
                return ErrorCode.LLM_TOKEN_LIMIT, False
        return ErrorCode.LLM_PROVIDER_FAILED, False

    if isinstance(exc, (httpx.ConnectError, httpx.ReadError)):
        return ErrorCode.LLM_PROVIDER_FAILED, True

    # JSON 解析失败等
    return ErrorCode.LLM_NO_RESPONSE, False


async def retry_with_backoff(
    func: Callable[[], Any],
    *,
    max_retries: int,
    initial_delay: float,
    model: str,
) -> T:
    """
    使用指数退避重试调用。

    Args:
        func: 异步可调用对象
        max_retries: 最大重试次数
        initial_delay: 初始延迟（秒）
        model: 模型名称，用于日志

    Returns:
        func 的返回值

    Raises:
        LLMProviderError: 重试耗尽或不可重试错误
    """
    last_exception: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            return await func()
        except Exception as exc:
            last_exception = exc
            error_code, retryable = classify_llm_error(exc)

            if not retryable or attempt >= max_retries:
                logger.warning(
                    "LLM 调用失败，不再重试",
                    extra={
                        "model": model,
                        "attempt": attempt + 1,
                        "error_code": error_code.name,
                        "retryable": retryable,
                        "error": str(exc),
                    },
                )
                raise LLMProviderError(str(exc)) from exc

            delay = initial_delay * (2**attempt)
            logger.info(
                "LLM 调用失败，准备重试",
                extra={
                    "model": model,
                    "attempt": attempt + 1,
                    "error_code": error_code.name,
                    "retry_delay_seconds": delay,
                    "error": str(exc),
                },
            )
            await asyncio.sleep(delay)

    # 理论上不会到达这里
    raise LLMProviderError(f"重试耗尽: {last_exception}") from last_exception


def record_llm_metrics(
    *,
    metrics: Any,
    model: str,
    role: str,
    status: str,
    duration_seconds: float,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float,
) -> None:
    """
    记录 LLM 调用指标。

    Args:
        metrics: Metrics 实例
        model: 模型名称
        role: 角色（fast/strong）
        status: 状态（success/error）
        duration_seconds: 耗时（秒）
        prompt_tokens: 输入 token 数
        completion_tokens: 输出 token 数
        cost_usd: 成本（美元）
    """
    if not hasattr(metrics, "llm_requests"):
        return

    metrics.llm_requests.labels(model=model, role=role, status=status).inc()
    metrics.llm_duration.labels(model=model).observe(duration_seconds)
    metrics.llm_tokens.labels(model=model, kind="prompt").inc(prompt_tokens)
    metrics.llm_tokens.labels(model=model, kind="completion").inc(completion_tokens)
    metrics.llm_cost.labels(model=model).inc(cost_usd)
