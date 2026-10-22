from __future__ import annotations

from enum import StrEnum

import httpx

from rag_center.core.config import get_settings
from rag_center.infrastructure.providers.llm.openai_compatible import OpenAICompatibleLLMProvider


class LLMRole(StrEnum):
    FAST = "fast"
    STRONG = "strong"


class LLMRoleRouter:
    @staticmethod
    def model_for(role: LLMRole) -> str:
        if role == LLMRole.STRONG:
            return get_settings().llm_strong_model or get_settings().llm_model
        return get_settings().llm_model

    @classmethod
    def provider_for(
        cls,
        role: LLMRole,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> OpenAICompatibleLLMProvider:
        provider = OpenAICompatibleLLMProvider(model=cls.model_for(role), client=client)
        provider.role = role.value
        return provider
