from __future__ import annotations

import atexit
import contextvars
import functools
import inspect
import json
import logging
import logging.handlers
import os
import queue
import re
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, ClassVar, cast
from uuid import uuid4

from rag_center.core.config import Settings, get_settings

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
job_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("job_id", default="-")
_listener: logging.handlers.QueueListener | None = None

# 敏感字段名，用于脱敏
SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "password",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "client_secret",
    "bearer",
}

# Bearer token 和 sk- 前缀匹配
BEARER_PATTERN = re.compile(r"Bearer\s+\S+", re.IGNORECASE)
SK_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")
# URL 中的凭据：https://user:pass@host 或 ?token=xxx
URL_CRED_PATTERN = re.compile(r"(https?://)[^:]+:[^@]+@")
URL_PARAM_PATTERN = re.compile(r"([?&](token|key|password|secret)=)[^&\s]+", re.IGNORECASE)


def redact_sensitive(text: str) -> str:
    """脱敏文本中的敏感信息。"""
    if not text:
        return text
    text = BEARER_PATTERN.sub("Bearer [REDACTED]", text)
    text = SK_PATTERN.sub("sk-[REDACTED]", text)
    text = URL_CRED_PATTERN.sub(r"\1[REDACTED]@", text)
    text = URL_PARAM_PATTERN.sub(r"\1[REDACTED]", text)
    return text


def redact_dict(data: Any, max_depth: int = 10) -> Any:
    """递归脱敏字典中的敏感键值。"""
    if max_depth <= 0:
        return data
    if isinstance(data, dict):
        return {
            k: "[REDACTED]" if k.lower() in SENSITIVE_KEYS else redact_dict(v, max_depth - 1)
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [redact_dict(item, max_depth - 1) for item in data]
    if isinstance(data, str):
        return redact_sensitive(data)
    return data


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get("-")
        record.job_id = job_id_var.get("-")
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_data: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "job_id": getattr(record, "job_id", "-"),
            "service": os.getenv("RAG_SERVICE_NAME", "rag-center"),
        }

        # 输出 extra 结构化字段
        for key, value in record.__dict__.items():
            if key not in (
                "name",
                "msg",
                "args",
                "created",
                "filename",
                "funcName",
                "levelname",
                "levelno",
                "lineno",
                "module",
                "msecs",
                "message",
                "pathname",
                "process",
                "processName",
                "relativeCreated",
                "thread",
                "threadName",
                "exc_info",
                "exc_text",
                "stack_info",
                "request_id",
                "job_id",
            ):
                log_data[key] = redact_dict(value)

        # 输出异常堆栈
        if record.exc_info:
            exc_text = self.formatException(record.exc_info)
            log_data["exc_info"] = redact_sensitive(exc_text)

        return json.dumps(log_data, ensure_ascii=False)


class ColorFormatter(logging.Formatter):
    COLORS: ClassVar[dict[int, str]] = {
        logging.DEBUG: "\033[36m",
        logging.INFO: "\033[32m",
        logging.WARNING: "\033[33m",
        logging.ERROR: "\033[31m",
        logging.CRITICAL: "\033[35m",
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelno, "")
        original_levelname = record.levelname
        record.levelname = f"{color}{record.levelname}{self.RESET}" if color else record.levelname
        try:
            return super().format(record)
        finally:
            record.levelname = original_levelname


def generate_request_id() -> str:
    return str(uuid4())


def set_request_id(request_id: str) -> contextvars.Token[str]:
    return request_id_var.set(request_id)


def reset_request_id(token: contextvars.Token[str]) -> None:
    request_id_var.reset(token)


def get_request_id() -> str:
    return request_id_var.get("-")


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def configure_logging(settings: Settings | None = None) -> None:
    global _listener
    if _listener is not None:
        return

    active_settings = settings or get_settings()
    os.makedirs(active_settings.log_dir, exist_ok=True)

    fmt = (
        "%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d | "
        "request_id=%(request_id)s | %(message)s"
    )
    datefmt = "%Y-%m-%d %H:%M:%S"

    request_filter = RequestIdFilter()
    # Celery 会将 sys.stderr 重定向到日志器；使用原始流避免队列监听器递归写日志。
    console_handler = logging.StreamHandler(sys.__stderr__)
    console_handler.setFormatter(
        JsonFormatter() if active_settings.log_json else ColorFormatter(fmt=fmt, datefmt=datefmt)
    )

    size_handler = logging.handlers.RotatingFileHandler(
        os.path.join(active_settings.log_dir, "app.log"),
        maxBytes=active_settings.log_max_bytes,
        backupCount=active_settings.log_backup_count,
        encoding="utf-8",
    )
    size_handler.setFormatter(
        JsonFormatter() if active_settings.log_json else logging.Formatter(fmt=fmt, datefmt=datefmt)
    )

    error_handler = logging.handlers.RotatingFileHandler(
        os.path.join(active_settings.log_dir, "error.log"),
        maxBytes=active_settings.log_error_max_bytes,
        backupCount=active_settings.log_error_backup_count,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(
        JsonFormatter() if active_settings.log_json else logging.Formatter(fmt=fmt, datefmt=datefmt)
    )

    time_handler = logging.handlers.TimedRotatingFileHandler(
        os.path.join(active_settings.log_dir, "app.daily.log"),
        when="midnight",
        backupCount=active_settings.log_daily_backup_count,
        encoding="utf-8",
    )
    time_handler.setFormatter(
        JsonFormatter() if active_settings.log_json else logging.Formatter(fmt=fmt, datefmt=datefmt)
    )

    log_queue: queue.Queue[logging.LogRecord] = queue.Queue(-1)
    queue_handler = logging.handlers.QueueHandler(log_queue)
    queue_handler.addFilter(request_filter)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(active_settings.log_level.upper())
    root.addHandler(queue_handler)

    _listener = logging.handlers.QueueListener(
        log_queue,
        console_handler,
        size_handler,
        error_handler,
        time_handler,
        respect_handler_level=True,
    )
    _listener.start()
    atexit.register(stop_logging)


def stop_logging() -> None:
    global _listener
    if _listener is None:
        return
    _listener.stop()
    _listener = None


def safe_json(value: Any, *, max_length: int | None = None) -> str:
    limit = max_length or get_settings().log_body_max_chars
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        text = str(value)
    return truncate_text(text, limit)


def truncate_text(value: Any, max_length: int | None = None) -> str:
    limit = max_length or get_settings().log_body_max_chars
    text = value if isinstance(value, str) else str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...(truncated, total={len(text)})"


def log_api_call[**P, R](func: Callable[P, R]) -> Callable[P, R]:
    logger = get_logger(func.__module__)
    function_name = getattr(func, "__name__", func.__class__.__name__)

    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            started_at = time.perf_counter()
            logger.info(
                "API_REQUEST | %s | args=%s | kwargs=%s",
                function_name,
                safe_json(args),
                safe_json(kwargs),
            )
            try:
                result = await func(*args, **kwargs)
                cost_ms = (time.perf_counter() - started_at) * 1000
                logger.info(
                    "API_RESPONSE | %s | cost=%.2fms | result=%s",
                    function_name,
                    cost_ms,
                    safe_json(result),
                )
                return result
            except Exception:
                cost_ms = (time.perf_counter() - started_at) * 1000
                logger.exception("API_EXCEPTION | %s | cost=%.2fms", function_name, cost_ms)
                raise

        return cast(Callable[P, R], async_wrapper)

    @functools.wraps(func)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        started_at = time.perf_counter()
        logger.info(
            "API_REQUEST | %s | args=%s | kwargs=%s",
            function_name,
            safe_json(args),
            safe_json(kwargs),
        )
        try:
            result = func(*args, **kwargs)
            cost_ms = (time.perf_counter() - started_at) * 1000
            logger.info(
                "API_RESPONSE | %s | cost=%.2fms | result=%s",
                function_name,
                cost_ms,
                safe_json(result),
            )
            return result
        except Exception:
            cost_ms = (time.perf_counter() - started_at) * 1000
            logger.exception("API_EXCEPTION | %s | cost=%.2fms", function_name, cost_ms)
            raise

    return cast(Callable[P, R], sync_wrapper)


def log_llm_request(*, model: str, prompt: Any, extra: dict[str, Any] | None = None) -> None:
    if not get_settings().log_model_content:
        return
    logger = get_logger("rag_center.llm")
    logger.info(
        "LLM_REQUEST | model=%s | prompt=%s | extra=%s",
        model,
        safe_json(prompt, max_length=get_settings().log_llm_max_chars),
        safe_json(extra or {}),
    )


def log_llm_response(
    *,
    model: str,
    response: Any,
    cost_ms: float,
    extra: dict[str, Any] | None = None,
) -> None:
    if not get_settings().log_model_content:
        return
    logger = get_logger("rag_center.llm")
    logger.info(
        "LLM_RESPONSE | model=%s | cost=%.2fms | response=%s | extra=%s",
        model,
        cost_ms,
        safe_json(response, max_length=get_settings().log_llm_max_chars),
        safe_json(extra or {}),
    )


def log_llm_error(
    *,
    model: str,
    error: Exception,
    cost_ms: float,
    prompt: Any | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    if not get_settings().log_model_content:
        return
    logger = get_logger("rag_center.llm")
    logger.exception(
        "LLM_EXCEPTION | model=%s | cost=%.2fms | error_type=%s | error=%s | prompt=%s | extra=%s",
        model,
        cost_ms,
        type(error).__name__,
        str(error),
        safe_json(prompt, max_length=get_settings().log_llm_max_chars)
        if prompt is not None
        else "-",
        safe_json(extra or {}),
    )
