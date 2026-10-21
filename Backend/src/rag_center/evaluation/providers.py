"""按需加载评测依赖，普通 API 和配置命令无需安装 eval extra。"""

from importlib import import_module

from rag_center.core.config import get_settings


def optional_module(name: str):
    try:
        return import_module(name)
    except ImportError as exc:
        raise RuntimeError("评测依赖未安装；请运行 uv sync --extra eval") from exc


def build_ragas_llm():
    settings = get_settings()
    key = settings.llm_api_key.get_secret_value() or settings.model_api_key.get_secret_value()
    if not key:
        raise ValueError("评测需要配置 RAG_LLM_API_KEY 或 RAG_MODEL_API_KEY")
    client = optional_module("openai").OpenAI(
        base_url=settings.llm_base_url or settings.model_base_url, api_key=key
    )
    return optional_module("ragas.llms").llm_factory(
        settings.llm_model, client=client, max_tokens=4096
    )


def load_ragas_metrics():
    module = optional_module("ragas.metrics")
    return tuple(
        metric() if isinstance(metric, type) else metric
        for metric in (module.context_precision, module.context_recall)
    )
