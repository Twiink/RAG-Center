from fastapi import APIRouter

from rag_center.api.v1.routes import auth, documents, index_jobs, knowledge_bases, rag
from rag_center.schemas.common import ApiErrorResponse

api_router = APIRouter(
    prefix="/api/v1",
    responses={
        status: {"model": ApiErrorResponse, "description": description}
        for status, description in {
            400: "请求参数错误",
            401: "凭据无效或缺失",
            403: "权限不足",
            404: "资源不存在",
            409: "资源或任务冲突",
            422: "请求校验失败",
            429: "超出调用限制",
            500: "服务内部错误",
            502: "上游服务异常",
            503: "服务暂不可用",
        }.items()
    },
)
api_router.include_router(auth.router)
api_router.include_router(knowledge_bases.router)
api_router.include_router(documents.router)
api_router.include_router(index_jobs.router)
api_router.include_router(rag.router)
