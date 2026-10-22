"""离线链路日志分析。

读取 ``log_event`` 输出的 JSON 行日志，统计检索各阶段延迟与瓶颈、Research 各阶段耗时、
LLM 调用成本排名、租户每日成本趋势以及典型故障场景，供 ``rag-center analyze-traces`` 使用。
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rag_center.core import log_events

FAULT_NAMES: tuple[str, ...] = (
    "llm_rate_limit",
    "llm_timeout",
    "llm_invalid_json",
    "rerank_degraded",
    "retrieval_backend_error",
)
_LLM_FAULTS: dict[str, str] = {
    "LLM_RATE_LIMIT": "llm_rate_limit",
    "LLM_TIMEOUT": "llm_timeout",
    "LLM_INVALID_JSON": "llm_invalid_json",
}
_RETRIEVAL_BACKEND_CODES: frozenset[str] = frozenset(
    {"ELASTICSEARCH_UNAVAILABLE", "ELASTICSEARCH_TIMEOUT", "EMBEDDING_TIMEOUT"}
)
_TOP_TENANTS = 5
_MAX_TRACE_IDS = 5
_BAR_WIDTH = 20


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


@dataclass
class _StageStats:
    """单个阶段的次数、错误数与耗时样本。"""

    count: int = 0
    errors: int = 0
    durations: list[float] = field(default_factory=list)

    def add(self, event: dict[str, Any]) -> None:
        self.count += 1
        if event.get("status") == log_events.STATUS_ERROR:
            self.errors += 1
        duration = _to_float(event.get("duration_ms"))
        if duration is not None:
            self.durations.append(duration)

    def summary(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "errors": self.errors,
            "p50": percentile(self.durations, 50),
            "p90": percentile(self.durations, 90),
            "p99": percentile(self.durations, 99),
        }


@dataclass
class _LLMStats:
    """LLM 调用次数、Token 与成本累计。"""

    calls: int = 0
    errors: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0

    def add(self, event: dict[str, Any]) -> None:
        self.calls += 1
        if event.get("status") == log_events.STATUS_ERROR:
            self.errors += 1
        self.prompt_tokens += int(_to_float(event.get("prompt_tokens")) or 0)
        self.completion_tokens += int(_to_float(event.get("completion_tokens")) or 0)
        self.cost += _to_float(event.get("cost")) or 0.0

    def summary(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "errors": self.errors,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost": round(self.cost, 6),
        }


def _ensure_utc(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def event_time(record: dict[str, Any]) -> datetime | None:
    """解析事件时间，依次尝试 timestamp、time、ts 字段；无时区信息时按 UTC 处理。"""
    for key in ("timestamp", "time", "ts"):
        raw = record.get(key)
        if not isinstance(raw, str) or not raw:
            continue
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return None


def _iter_records(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            text = raw_line.strip()
            if not text:
                continue
            try:
                record = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                yield record


def load_events(
    paths: Iterable[str | Path],
    *,
    since: str | datetime | None = None,
    until: str | datetime | None = None,
    tenant: str | None = None,
    model: str | None = None,
) -> list[dict[str, Any]]:
    """读取并过滤日志事件；非 JSON 行、非对象行与缺少事件名的行直接跳过。

    - since/until：按事件时间过滤，设置后缺少时间的事件会被丢弃；
    - tenant：按 tenant_id 精确匹配；
    - model：只过滤其他模型的 llm.call 事件，其余事件保留以便关联分析。
    """
    start = _ensure_utc(since)
    end = _ensure_utc(until)
    events: list[dict[str, Any]] = []
    for path in paths:
        for record in _iter_records(Path(path)):
            name = record.get("event") or record.get("message")
            if not isinstance(name, str) or not name:
                continue
            if start is not None or end is not None:
                moment = event_time(record)
                if moment is None:
                    continue
                if (start is not None and moment < start) or (end is not None and moment > end):
                    continue
            if tenant is not None and str(record.get("tenant_id")) != tenant:
                continue
            if model is not None and name == log_events.LLM_CALL and record.get("model") != model:
                continue
            events.append({**record, "event": name})
    return events


def percentile(values: Iterable[float], q: float) -> float:
    """最近秩法分位数，q 取值 0-100；空序列返回 0.0。"""
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    rank = max(1, math.ceil(q * len(ordered) / 100))
    return ordered[min(rank, len(ordered)) - 1]


def _trace_key(event: dict[str, Any]) -> str | None:
    value = event.get("trace_id") or event.get("request_id")
    return str(value) if value else None


def classify_fault(event: dict[str, Any]) -> str | None:
    """把单条事件归入典型故障场景，不属于任何场景时返回 None。"""
    name = event.get("event")
    code = event.get("error_code")
    if name == log_events.LLM_CALL and isinstance(code, str):
        return _LLM_FAULTS.get(code)
    if name == log_events.DEGRADATION and event.get("kind") == "rerank":
        return "rerank_degraded"
    if (
        name == log_events.RETRIEVAL_STAGE
        and event.get("status") == log_events.STATUS_ERROR
        and code in _RETRIEVAL_BACKEND_CODES
    ):
        return "retrieval_backend_error"
    return None


def analyze(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """聚合事件，输出检索延迟、Research 耗时、LLM 成本与故障场景报告。"""
    retrieval: defaultdict[str, _StageStats] = defaultdict(_StageStats)
    research: defaultdict[str, _StageStats] = defaultdict(_StageStats)
    stop_reasons: defaultdict[str, int] = defaultdict(int)
    by_model: defaultdict[str, _LLMStats] = defaultdict(_LLMStats)
    by_tenant: defaultdict[str, _LLMStats] = defaultdict(_LLMStats)
    daily: dict[str, dict[str, float]] = {}
    faults: dict[str, dict[str, Any]] = {
        name: {"count": 0, "trace_ids": []} for name in FAULT_NAMES
    }
    total = 0

    for event in events:
        total += 1
        name = event.get("event")
        stage = str(event.get("stage") or "unknown")
        if name == log_events.RETRIEVAL_STAGE:
            retrieval[stage].add(event)
        elif name == log_events.RESEARCH_STAGE:
            research[stage].add(event)
            reason = event.get("stop_reason")
            if reason:
                stop_reasons[str(reason)] += 1
        elif name == log_events.LLM_CALL:
            by_model[str(event.get("model") or "unknown")].add(event)
            tenant_id = event.get("tenant_id")
            moment = event_time(event)
            if tenant_id is not None:
                tenant_key = str(tenant_id)
                by_tenant[tenant_key].add(event)
                if moment is not None:
                    per_day = daily.setdefault(moment.astimezone(UTC).date().isoformat(), {})
                    cost = _to_float(event.get("cost")) or 0.0
                    per_day[tenant_key] = per_day.get(tenant_key, 0.0) + cost
        fault = classify_fault(event)
        if fault is not None:
            bucket = faults[fault]
            bucket["count"] += 1
            trace = _trace_key(event)
            trace_ids: list[str] = bucket["trace_ids"]
            if trace and trace not in trace_ids and len(trace_ids) < _MAX_TRACE_IDS:
                trace_ids.append(trace)

    retrieval_summary = {stage: stats.summary() for stage, stats in sorted(retrieval.items())}
    candidates = [
        (summary["p90"], stage)
        for stage, summary in retrieval_summary.items()
        if retrieval[stage].durations
    ]
    ranked_tenants = sorted(by_tenant.items(), key=lambda item: item[1].cost, reverse=True)
    top_tenants = {tenant for tenant, _ in ranked_tenants[:_TOP_TENANTS]}
    daily_cost: dict[str, dict[str, float]] = {}
    for day in sorted(daily):
        selected = {
            tenant: round(cost, 6)
            for tenant, cost in sorted(daily[day].items())
            if tenant in top_tenants
        }
        if selected:
            daily_cost[day] = selected

    return {
        "total_events": total,
        "retrieval": retrieval_summary,
        "bottleneck": max(candidates)[1] if candidates else None,
        "research_stages": {stage: stats.summary() for stage, stats in research.items()},
        "stop_reasons": dict(sorted(stop_reasons.items())),
        "llm_by_model": {
            model: stats.summary()
            for model, stats in sorted(
                by_model.items(), key=lambda item: item[1].cost, reverse=True
            )
        },
        "llm_by_tenant": {tenant: stats.summary() for tenant, stats in ranked_tenants},
        "daily_cost": daily_cost,
        "faults": faults,
    }


def _format_stage(label: str, stats: dict[str, Any]) -> str:
    return (
        f"  {label:<12} 次数={stats['count']:<5} 错误={stats['errors']:<4} "
        f"p50={stats['p50']:.1f} p90={stats['p90']:.1f} p99={stats['p99']:.1f}"
    )


def _format_llm(label: str, stats: dict[str, Any]) -> str:
    return (
        f"  {label:<16} 调用={stats['calls']:<5} 错误={stats['errors']:<4} "
        f"prompt={stats['prompt_tokens']:<8} completion={stats['completion_tokens']:<8} "
        f"成本={stats['cost']:.6f}"
    )


def render_text(report: dict[str, Any]) -> str:
    """将分析报告渲染为纯文本，Research 阶段以 # 条形长度表示 p90 相对耗时。"""
    lines = [f"事件总数: {report.get('total_events', 0)}", "", "[检索阶段延迟] 单位毫秒"]
    retrieval: dict[str, Any] = report.get("retrieval") or {}
    lines.extend(_format_stage(stage, stats) for stage, stats in retrieval.items())
    if not retrieval:
        lines.append("  无数据")
    lines += ["", "[瓶颈阶段]", f"  {report.get('bottleneck') or '无数据'}"]

    lines += ["", "[Research 阶段耗时] 单位毫秒"]
    research: dict[str, Any] = report.get("research_stages") or {}
    peak = max((stats["p90"] for stats in research.values()), default=0.0)
    for stage, stats in research.items():
        width = round(stats["p90"] / peak * _BAR_WIDTH) if peak else 0
        lines.append(f"{_format_stage(stage, stats)} {'#' * width}")
    if not research:
        lines.append("  无数据")

    lines += ["", "[停止原因]"]
    stop_reasons: dict[str, int] = report.get("stop_reasons") or {}
    lines.extend(f"  {reason:<20} {count}" for reason, count in stop_reasons.items())
    if not stop_reasons:
        lines.append("  无数据")

    lines += ["", "[LLM 调用 (按模型)] 按成本降序"]
    by_model: dict[str, Any] = report.get("llm_by_model") or {}
    lines.extend(_format_llm(model, stats) for model, stats in by_model.items())
    if not by_model:
        lines.append("  无数据")

    lines += ["", "[租户成本] 按成本降序"]
    by_tenant: dict[str, Any] = report.get("llm_by_tenant") or {}
    lines.extend(_format_llm(tenant, stats) for tenant, stats in by_tenant.items())
    if not by_tenant:
        lines.append("  无数据")

    lines += ["", f"[每日成本趋势] 成本最高的 {_TOP_TENANTS} 个租户"]
    daily_cost: dict[str, dict[str, float]] = report.get("daily_cost") or {}
    for day, tenants in daily_cost.items():
        detail = ", ".join(f"{tenant}={cost:.6f}" for tenant, cost in tenants.items())
        lines.append(f"  {day} {detail}")
    if not daily_cost:
        lines.append("  无数据")

    lines += ["", "[故障场景]"]
    for name, fault in (report.get("faults") or {}).items():
        traces = ", ".join(fault["trace_ids"]) or "-"
        lines.append(f"  {name:<24} 次数={fault['count']:<4} trace={traces}")
    return "\n".join(lines) + "\n"
