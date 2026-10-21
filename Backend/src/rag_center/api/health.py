from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from rag_center.core.container import get_container
from rag_center.core.diagnostics import inspect_dependencies

health_router = APIRouter(tags=["health"])


@health_router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@health_router.get("/health/ready", response_model=None)
async def ready(request: Request) -> JSONResponse:
    report = await inspect_dependencies(get_container(request.app))
    return JSONResponse(report, status_code=200 if report["status"] == "ok" else 503)


@health_router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    return Response(
        generate_latest(get_container(request.app).metrics.registry),
        headers={"Content-Type": CONTENT_TYPE_LATEST},
    )
