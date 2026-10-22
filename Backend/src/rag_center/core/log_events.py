"""结构化事件日志的事件名与统一写入入口。

关键路径（LLM 调用、检索阶段、Research 状态机、Worker 与 Scheduler）统一通过
``log_event`` 输出 JSON 日志。事件名固定为本模块常量，``analyze-traces`` 依赖这些名称离线分析。
公共字段约定：trace_id、tenant_id、duration_ms、status、error_code。
"""

from __future__ import annotations

import logging
from typing import Any, Final

LLM_CALL: Final[str] = "llm.call"
RETRIEVAL_STAGE: Final[str] = "retrieval.stage"
RESEARCH_STAGE: Final[str] = "research.stage"
RESEARCH_TRANSITION: Final[str] = "research.transition"
DEGRADATION: Final[str] = "degradation"
WORKER_CLAIM: Final[str] = "worker.claim"
WORKER_HEARTBEAT: Final[str] = "worker.heartbeat"
WORKER_PUBLISH: Final[str] = "worker.publish"
SCHEDULER_DISPATCH: Final[str] = "scheduler.dispatch"
SCHEDULER_RECLAIM: Final[str] = "scheduler.reclaim"
INDEX_JOB_FAILED: Final[str] = "index_job.failed"

STATUS_OK: Final[str] = "ok"
STATUS_ERROR: Final[str] = "error"

# LogRecord 自带属性；extra 中出现同名键会触发 KeyError，需要改名后再写入
_RESERVED_KEYS: Final[frozenset[str]] = frozenset(
    set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}
)


def safe_extra(event: str, fields: dict[str, Any]) -> dict[str, Any]:
    """构造 logging extra：丢弃 None 值，保留字段名冲突时追加下划线后缀。"""
    extra: dict[str, Any] = {"event": event}
    for key, value in fields.items():
        if value is None:
            continue
        extra[f"{key}_" if key in _RESERVED_KEYS else key] = value
    return extra


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    """输出一条结构化事件日志，日志消息与 event 字段均为事件名。"""
    if not logger.isEnabledFor(level):
        return
    logger.log(level, event, extra=safe_extra(event, fields))
