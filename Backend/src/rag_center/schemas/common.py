from typing import Any

from pydantic import BaseModel, Field

from rag_center.core.exceptions import ErrorCode
from rag_center.core.logging import get_request_id


class ApiResponse[T](BaseModel):
    code: int = 0
    msg: str = "success"
    data: T
    request_id: str = Field(default_factory=get_request_id)


class ApiErrorResponse(BaseModel):
    """业务接口统一错误契约，校验错误也使用相同封装。"""

    code: int
    msg: str
    data: Any = None
    request_id: str


def success_response(data: Any, request_id: str | None = None) -> dict:
    return {"code": 0, "msg": "success", "data": data, "request_id": request_id or get_request_id()}


def accepted_response(data: Any, request_id: str | None = None) -> dict:
    return {
        "code": 0,
        "msg": "accepted",
        "data": data,
        "request_id": request_id or get_request_id(),
    }


def error_response(
    code: int | ErrorCode, msg: str | None = None, data: Any = None, request_id: str | None = None
) -> dict:
    code_value, message = (
        (code.code, msg or code.msg) if isinstance(code, ErrorCode) else (code, msg or "请求失败")
    )
    return {
        "code": code_value,
        "msg": message,
        "data": data,
        "request_id": request_id or get_request_id(),
    }
