"""analyze-traces 离线日志分析测试。"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from rag_center.core.log_events import LLM_CALL, log_event, safe_extra
from rag_center.core.trace_analysis import (
    FAULT_NAMES,
    analyze,
    load_events,
    percentile,
    render_text,
)

FIXTURE = Path(__file__).parent / "fixtures" / "traces" / "faults.jsonl"


def test_load_events_skips_invalid_lines() -> None:
    events = load_events([FIXTURE])

    assert len(events) == 22
    assert all(isinstance(event["event"], str) for event in events)


def test_time_filters_drop_events_outside_range() -> None:
    since = datetime(2026, 10, 21, tzinfo=UTC)
    until = datetime(2026, 10, 20, 23, 59, 59, tzinfo=UTC)

    assert len(load_events([FIXTURE], since=since)) == 11
    assert len(load_events([FIXTURE], until=until)) == 11
    # 无时区的时间按 UTC 处理
    assert len(load_events([FIXTURE], since=datetime(2026, 10, 21))) == 11


def test_tenant_filter_matches_tenant_id() -> None:
    events = load_events([FIXTURE], tenant="t1")

    assert len(events) == 13
    assert {event["tenant_id"] for event in events} == {"t1"}


def test_model_filter_only_drops_other_llm_calls() -> None:
    events = load_events([FIXTURE], model="qwen-max")
    llm_calls = [event for event in events if event["event"] == LLM_CALL]

    assert len(events) == 18
    assert len(llm_calls) == 3
    assert {event["model"] for event in llm_calls} == {"qwen-max"}


@pytest.mark.parametrize(("q", "expected"), [(50, 5.0), (90, 9.0), (99, 10.0)])
def test_percentile_nearest_rank(q: float, expected: float) -> None:
    assert percentile(range(1, 11), q) == expected


def test_percentile_empty_returns_zero() -> None:
    assert percentile([], 90) == 0.0


def test_analyze_reports_bottleneck_and_costs() -> None:
    report = analyze(load_events([FIXTURE]))

    assert report["bottleneck"] == "rerank"
    assert report["retrieval"]["rerank"]["p90"] == 900.0
    assert report["retrieval"]["bm25"]["errors"] == 1
    assert set(report["research_stages"]) == {"planner", "retrieval", "decider", "finalizer"}
    assert report["stop_reasons"] == {"sufficient": 1}
    qwen_max = report["llm_by_model"]["qwen-max"]
    assert qwen_max["calls"] == 3
    assert qwen_max["errors"] == 1
    assert qwen_max["cost"] == pytest.approx(0.031)
    assert next(iter(report["llm_by_model"])) == "qwen-max"
    assert report["llm_by_tenant"]["t1"]["cost"] == pytest.approx(0.022)
    assert report["daily_cost"]["2026-10-20"] == {"t1": pytest.approx(0.022)}
    assert report["daily_cost"]["2026-10-21"] == {"t2": pytest.approx(0.0114)}


def test_analyze_detects_each_fault_once() -> None:
    report = analyze(load_events([FIXTURE]))

    assert tuple(report["faults"]) == FAULT_NAMES
    for name in FAULT_NAMES:
        fault = report["faults"][name]
        assert fault["count"] == 1, name
        assert len(fault["trace_ids"]) == 1, name


def test_analyze_empty_input_keeps_all_fault_names() -> None:
    report = analyze([])

    assert report["bottleneck"] is None
    assert all(report["faults"][name]["count"] == 0 for name in FAULT_NAMES)


def test_render_text_has_sections_without_emoji() -> None:
    text = render_text(analyze(load_events([FIXTURE])))

    for header in (
        "检索阶段延迟",
        "瓶颈阶段",
        "Research 阶段耗时",
        "停止原因",
        "LLM 调用",
        "租户成本",
        "每日成本趋势",
        "故障场景",
    ):
        assert header in text
    assert not any(ord(char) >= 0x1F000 or 0x2600 <= ord(char) <= 0x27BF for char in text)


def test_log_event_renames_reserved_keys_and_drops_none(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("rag_center.tests.log_events")
    extra = safe_extra("demo", {"name": "x", "status": "ok", "missing": None})

    assert extra == {"event": "demo", "name_": "x", "status": "ok"}
    with caplog.at_level(logging.INFO, logger=logger.name):
        log_event(logger, LLM_CALL, model="m", module="planner", cost=None)
    record = caplog.records[-1]
    assert record.getMessage() == LLM_CALL
    assert record.__dict__["event"] == LLM_CALL
    assert record.__dict__["module_"] == "planner"
    assert "cost" not in record.__dict__


def test_analyze_traces_json_format():
    """测试 JSON 格式输出包含所有故障类型"""
    import json
    from pathlib import Path

    from typer.testing import CliRunner

    from rag_center.cli.commands import app

    runner = CliRunner()
    fixture_path = Path(__file__).parent / "fixtures" / "traces" / "faults.jsonl"

    result = runner.invoke(
        app, ["analyze-traces", "--log-file", str(fixture_path), "--format", "json"]
    )

    assert result.exit_code == 0
    report = json.loads(result.stdout)

    assert "faults" in report
    fault_names = list(report["faults"].keys())

    assert "llm_rate_limit" in fault_names
    assert "llm_timeout" in fault_names
    assert "llm_invalid_json" in fault_names
    assert "rerank_degraded" in fault_names
    assert "retrieval_backend_error" in fault_names
    assert len(fault_names) == 5
