"""日志脱敏测试。"""

import logging

from rag_center.core.logging import JsonFormatter, redact_dict, redact_sensitive


def test_redact_sensitive_bearer_token():
    text = "Authorization: Bearer sk-abc123def456"
    result = redact_sensitive(text)
    assert "sk-abc123def456" not in result
    assert "[REDACTED]" in result


def test_redact_sensitive_sk_prefix():
    text = "API key is sk-proj-1234567890abcdef1234567890"
    result = redact_sensitive(text)
    assert "sk-proj-" not in result
    assert "sk-[REDACTED]" in result


def test_redact_sensitive_url_credentials():
    text = "Connecting to https://user:password@example.com/api"
    result = redact_sensitive(text)
    assert "password" not in result
    assert "[REDACTED]@" in result


def test_redact_sensitive_url_params():
    text = "GET /api?token=secret123&other=value"
    result = redact_sensitive(text)
    assert "secret123" not in result
    assert "token=[REDACTED]" in result


def test_redact_dict_sensitive_keys():
    data = {
        "api_key": "secret",
        "password": "hunter2",
        "normal_field": "visible",
    }
    result = redact_dict(data)
    assert result["api_key"] == "[REDACTED]"
    assert result["password"] == "[REDACTED]"
    assert result["normal_field"] == "visible"


def test_redact_dict_nested():
    data = {
        "outer": {
            "api_key": "secret",
            "inner": {
                "token": "hidden",
                "safe": "visible",
            },
        }
    }
    result = redact_dict(data)
    assert result["outer"]["api_key"] == "[REDACTED]"
    assert result["outer"]["inner"]["token"] == "[REDACTED]"
    assert result["outer"]["inner"]["safe"] == "visible"


def test_redact_dict_list():
    data = {
        "items": [
            {"api_key": "secret1"},
            {"password": "secret2"},
        ]
    }
    result = redact_dict(data)
    assert result["items"][0]["api_key"] == "[REDACTED]"
    assert result["items"][1]["password"] == "[REDACTED]"


def test_json_formatter_exception():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.ERROR,
        pathname="test.py",
        lineno=1,
        msg="Error occurred",
        args=(),
        exc_info=None,
    )
    try:
        raise ValueError("API key is sk-test1234567890abcdef1234567890")
    except ValueError:
        import sys

        record.exc_info = sys.exc_info()

    result = formatter.format(record)
    assert "sk-test" not in result
    assert "sk-[REDACTED]" in result


def test_json_formatter_extra_fields():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg="Test message",
        args=(),
        exc_info=None,
    )
    record.custom_field = {"api_key": "secret", "data": "visible"}

    result = formatter.format(record)
    assert "[REDACTED]" in result
    assert "visible" in result
