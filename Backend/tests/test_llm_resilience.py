"""LLM 韧性测试。"""

import httpx
import pytest

from rag_center.core.exceptions import ErrorCode
from rag_center.infrastructure.providers.llm.base import LLMProviderError
from rag_center.infrastructure.providers.llm.resilience import (
    classify_llm_error,
    retry_with_backoff,
)


def test_classify_timeout():
    exc = httpx.TimeoutException("Request timeout")
    code, retryable = classify_llm_error(exc)
    assert code == ErrorCode.LLM_TIMEOUT
    assert retryable is True


def test_classify_rate_limit():
    response = httpx.Response(429, text="Rate limit exceeded")
    exc = httpx.HTTPStatusError("429", request=None, response=response)
    code, retryable = classify_llm_error(exc)
    assert code == ErrorCode.LLM_RATE_LIMIT
    assert retryable is True


def test_classify_server_error():
    response = httpx.Response(500, text="Internal server error")
    exc = httpx.HTTPStatusError("500", request=None, response=response)
    code, retryable = classify_llm_error(exc)
    assert code == ErrorCode.LLM_PROVIDER_FAILED
    assert retryable is True


def test_classify_token_limit():
    response = httpx.Response(400, text="Token limit exceeded")
    exc = httpx.HTTPStatusError("400", request=None, response=response)
    code, retryable = classify_llm_error(exc)
    assert code == ErrorCode.LLM_TOKEN_LIMIT
    assert retryable is False


def test_classify_connect_error():
    exc = httpx.ConnectError("Connection failed")
    code, retryable = classify_llm_error(exc)
    assert code == ErrorCode.LLM_PROVIDER_FAILED
    assert retryable is True


@pytest.mark.asyncio
async def test_retry_success_on_first_attempt():
    call_count = 0

    async def func():
        nonlocal call_count
        call_count += 1
        return "success"

    result = await retry_with_backoff(func, max_retries=2, initial_delay=0.01, model="test")
    assert result == "success"
    assert call_count == 1


@pytest.mark.asyncio
async def test_retry_success_on_second_attempt():
    call_count = 0

    async def func():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.TimeoutException("Timeout")
        return "success"

    result = await retry_with_backoff(func, max_retries=2, initial_delay=0.01, model="test")
    assert result == "success"
    assert call_count == 2


@pytest.mark.asyncio
async def test_retry_exhausted():
    call_count = 0

    async def func():
        nonlocal call_count
        call_count += 1
        raise httpx.TimeoutException("Timeout")

    with pytest.raises(LLMProviderError):
        await retry_with_backoff(func, max_retries=2, initial_delay=0.01, model="test")

    assert call_count == 3


@pytest.mark.asyncio
async def test_retry_non_retryable_error():
    call_count = 0

    async def func():
        nonlocal call_count
        call_count += 1
        response = httpx.Response(400, text="Token limit")
        raise httpx.HTTPStatusError("400", request=None, response=response)

    with pytest.raises(LLMProviderError):
        await retry_with_backoff(func, max_retries=2, initial_delay=0.01, model="test")

    assert call_count == 1
