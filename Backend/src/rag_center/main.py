import asyncio
from collections.abc import AsyncGenerator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException

from rag_center.api.health import health_router
from rag_center.api.v1.router import api_router
from rag_center.core.config import load_settings
from rag_center.core.container import ResourceContainer
from rag_center.core.exceptions import AppError, ErrorCode
from rag_center.core.logging import configure_logging, get_logger
from rag_center.core.middleware import request_response_log_middleware
from rag_center.core.tracing import instrument_fastapi, setup_tracing
from rag_center.schemas.common import error_response


def create_app(settings=None) -> FastAPI:
    """创建 FastAPI 应用；settings 可注入，导入模块不会建立外部连接。"""

    active_settings = settings or load_settings()
    logger = get_logger(__name__)

    # 进程级 OTel 初始化——未配置 endpoint 时为 NoOp，幂等，失败不中断启动。
    tracing_status = setup_tracing(active_settings)
    if tracing_status.status not in ("disabled", "initialized"):
        logger.warning(
            "OTel 追踪启动异常",
            extra={
                "status": tracing_status.status,
                "failures": list(tracing_status.instrumentation_failures),
            },
        )

    @asynccontextmanager
    async def app_lifespan(app: FastAPI) -> AsyncGenerator[None]:
        configure_logging(active_settings)
        executor = ThreadPoolExecutor(max_workers=active_settings.parser_max_concurrency)
        asyncio.get_running_loop().set_default_executor(executor)
        container = ResourceContainer.create(active_settings)
        app.state.container = container
        try:
            yield
        finally:
            try:
                await container.aclose()
            finally:
                executor.shutdown(wait=False)

    app = FastAPI(
        title="RAG Center",
        version="0.2.0",
        lifespan=app_lifespan,
    )
    app.state.settings = active_settings
    # FastAPI 请求级 OTel span 插桩（app 实例创建后才能调用）
    instrument_fastapi(app)
    app.middleware("http")(request_response_log_middleware)
    app.include_router(health_router)
    app.include_router(api_router)

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        logger.error(
            "APP_ERROR | path=%s | code=%s | name=%s", request.url.path, exc.code, exc.error_name
        )
        return JSONResponse(
            status_code=exc.http_status,
            content=error_response(exc.code, exc.msg, exc.data, request.state.request_id),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_response(
                ErrorCode.REQUEST_VALIDATION_ERROR,
                ErrorCode.REQUEST_VALIDATION_ERROR.msg,
                [
                    {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
                    for error in exc.errors()
                ],
                request.state.request_id,
            ),
        )

    @app.exception_handler(IntegrityError)
    async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
        duplicate = getattr(exc.orig, "sqlstate", None) == "23505"
        code = ErrorCode.DATA_DUPLICATE if duplicate else ErrorCode.DB_ERROR
        return JSONResponse(
            status_code=code.http_status,
            content=error_response(code, request_id=request.state.request_id),
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response(
                ErrorCode.NOT_FOUND if exc.status_code == 404 else ErrorCode.METHOD_ERROR,
                str(exc.detail),
                request_id=request.state.request_id,
            ),
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "UNHANDLED_EXCEPTION | path=%s | type=%s", request.url.path, type(exc).__name__
        )
        return JSONResponse(
            status_code=500,
            content=error_response(
                ErrorCode.SERVER_ERROR,
                ErrorCode.SERVER_ERROR.msg,
                request_id=request.state.request_id,
            ),
        )

    return app


app = create_app()
