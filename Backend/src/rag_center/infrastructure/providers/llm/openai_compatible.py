import json
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Literal

import httpx

from rag_center.core.config import get_settings
from rag_center.core.log_events import LLM_CALL, STATUS_OK, log_event
from rag_center.core.logging import get_logger, log_llm_error, log_llm_request, log_llm_response
from rag_center.core.usage import estimate_cost, get_usage_accumulator
from rag_center.infrastructure.providers.llm.base import LLMProvider, LLMProviderError
from rag_center.infrastructure.providers.llm.resilience import record_llm_metrics

_event_logger = get_logger("rag_center.llm.events")


class OpenAICompatibleLLMProvider(LLMProvider):
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = (base_url if base_url is not None else get_settings().llm_base_url).rstrip(
            "/"
        )
        self.api_key = (
            api_key if api_key is not None else get_settings().llm_api_key.get_secret_value()
        )
        self.model = model if model is not None else get_settings().llm_model
        self.timeout_seconds = (
            timeout_seconds if timeout_seconds is not None else get_settings().llm_timeout_seconds
        )
        self.client = client
        self.last_usage: dict = {}
        # 角色标记（fast/strong），由 LLMRoleRouter.provider_for 注入，用于指标和事件区分
        self.role: str = "fast"

    def _record_usage(self, cost_ms: float, status: str) -> None:
        """记录 LLM 用量到三个渠道：Prometheus 指标、UsageAccumulator、结构化事件日志。

        三者各自独立 try/except，任一失败不影响主流程。
        """
        usage = self.last_usage or {}
        prompt_tokens = int(usage.get("prompt_tokens", 0))
        completion_tokens = int(usage.get("completion_tokens", 0))
        cost_usd = estimate_cost("openai_compatible", self.model, prompt_tokens, completion_tokens)

        # 1. Prometheus 指标
        try:
            from rag_center.core.metrics import get_metrics

            metrics = get_metrics()
            record_llm_metrics(
                metrics=metrics,
                model=self.model,
                role=self.role,
                status=status,
                duration_seconds=cost_ms / 1000,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=cost_usd,
            )
        except Exception:
            pass

        # 2. 累加到当前请求的 UsageAccumulator（供服务层读取后写入月度配额）
        try:
            acc = get_usage_accumulator()
            acc.add("openai_compatible", self.model, prompt_tokens, completion_tokens)
        except Exception:
            pass

        # 3. 结构化事件日志（供 analyze-traces 离线分析）
        try:
            log_event(
                _event_logger,
                LLM_CALL,
                model=self.model,
                role=self.role,
                status=status,
                duration_ms=int(cost_ms),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=round(cost_usd, 6),
            )
        except Exception:
            pass

    @asynccontextmanager
    async def _client(self) -> AsyncGenerator[httpx.AsyncClient]:
        if self.client is not None:
            yield self.client
            return
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            yield client

    async def chat_json(
        self,
        *,
        system_prompt: str,
        user_payload: dict,
        temperature: float = 0.0,
        timeout_seconds: float | None = None,
        log_payload: bool = True,
        max_tokens: int | None = None,
        thinking: Literal["enabled", "disabled"] | None = None,
    ) -> dict:
        payload = {
            "model": self.model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False),
                },
            ],
            "response_format": {"type": "json_object"},
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if thinking is not None:
            payload["thinking"] = {"type": thinking}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        started_at = time.perf_counter()
        candidates = user_payload.get("candidates")
        candidate_count = len(candidates) if isinstance(candidates, list) else 0
        input_chars = len(json.dumps(user_payload, ensure_ascii=False))
        logged_prompt = (
            {"system_prompt": system_prompt, "user_payload": user_payload}
            if log_payload
            else {"payload_redacted": True}
        )
        log_llm_request(
            model=self.model,
            prompt=logged_prompt,
            extra={
                "provider": "openai_compatible",
                "base_url": self.base_url,
                "candidate_count": candidate_count,
                "input_chars": input_chars,
            },
        )

        try:
            request_timeout = timeout_seconds or self.timeout_seconds
            async with self._client() as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=request_timeout,
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            cost_ms = (time.perf_counter() - started_at) * 1000
            log_llm_error(
                model=self.model,
                error=exc,
                cost_ms=cost_ms,
                prompt=logged_prompt,
                extra={
                    "provider": "openai_compatible",
                    "base_url": self.base_url,
                    "candidate_count": candidate_count,
                    "input_chars": input_chars,
                },
            )
            raise LLMProviderError(f"llm request failed: {exc}") from exc

        try:
            body = response.json()
            usage = body.get("usage") if isinstance(body, dict) else None
            self.last_usage = usage if isinstance(usage, dict) else {}
            content = body["choices"][0]["message"]["content"]
            parsed = self._parse_json_content(content)
            cost_ms = (time.perf_counter() - started_at) * 1000
            log_llm_response(
                model=self.model,
                response=parsed if log_payload else {"payload_redacted": True},
                cost_ms=cost_ms,
                extra={
                    "provider": "openai_compatible",
                    "candidate_count": candidate_count,
                    "input_chars": input_chars,
                },
            )
            self._record_usage(cost_ms, STATUS_OK)
            return parsed
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            cost_ms = (time.perf_counter() - started_at) * 1000
            log_llm_error(
                model=self.model,
                error=exc,
                cost_ms=cost_ms,
                prompt=logged_prompt,
                extra={
                    "provider": "openai_compatible",
                    "candidate_count": candidate_count,
                    "input_chars": input_chars,
                },
            )
            raise LLMProviderError(f"llm response json parse failed: {exc}") from exc

    @staticmethod
    def _parse_json_content(content: str) -> dict:
        cleaned = content.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()
        parsed = json.loads(cleaned)
        if not isinstance(parsed, dict):
            raise json.JSONDecodeError("top-level JSON is not an object", cleaned, 0)
        return parsed
