from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from rag_center.cli.commands import app

runner = CliRunner()


@pytest.fixture
def mock_run_operation():
    with patch("rag_center.cli.commands.run_operation") as mock:
        yield mock


def test_jobs_stats(mock_run_operation):
    mock_run_operation.return_value = {"queued": 5, "running": 2, "failed": 1}
    result = runner.invoke(app, ["jobs", "stats"])
    assert result.exit_code == 0
    assert "queued" in result.stdout


def test_jobs_failures(mock_run_operation):
    mock_run_operation.return_value = {
        "failures": [
            {
                "job_id": "job1",
                "resource_id": "res1",
                "job_type": "index",
                "error_code": "LLM_TIMEOUT",
                "error_message": "超时",
                "updated_at": "2026-10-21T10:00:00",
            }
        ],
        "error_counts": {"LLM_TIMEOUT": 1},
    }
    result = runner.invoke(app, ["jobs", "failures", "--limit", "10"])
    assert result.exit_code == 0
    assert "job1" in result.stdout


def test_jobs_leases(mock_run_operation):
    mock_run_operation.return_value = [
        {
            "job_id": "job2",
            "resource_id": "res2",
            "lease_token": "token",
            "lease_expires_at": "2026-10-21T09:00:00",
            "expired_seconds": 120,
        }
    ]
    result = runner.invoke(app, ["jobs", "leases"])
    assert result.exit_code == 0
    assert "job2" in result.stdout


def test_jobs_lag(mock_run_operation):
    mock_run_operation.return_value = {"unpublished_count": 10, "oldest_age_seconds": 300}
    result = runner.invoke(app, ["jobs", "lag"])
    assert result.exit_code == 0
    assert "10" in result.stdout


def test_jobs_retry_success(mock_run_operation):
    mock_run_operation.return_value = {"status": "success", "job_id": "job3"}
    result = runner.invoke(app, ["jobs", "retry", "job3"])
    assert result.exit_code == 0
    assert "job3" in result.stdout
    assert "重新加入队列" in result.stdout


def test_jobs_retry_not_found(mock_run_operation):
    mock_run_operation.side_effect = ValueError("任务不存在: job999")
    result = runner.invoke(app, ["jobs", "retry", "job999"])
    assert result.exit_code == 1
    assert "错误" in result.stdout


def test_jobs_retry_wrong_status(mock_run_operation):
    mock_run_operation.side_effect = ValueError("只能重试失败任务，当前状态: running")
    result = runner.invoke(app, ["jobs", "retry", "job4"])
    assert result.exit_code == 1
    assert "错误" in result.stdout
