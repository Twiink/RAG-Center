import asyncio

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from rag_center.api.v1.deps import ApiDependencies
from rag_center.core.auth import TenantContext
from rag_center.core.container import active_container
from rag_center.core.idempotency import IdempotencyManager
from rag_center.infrastructure.http.clients import ExternalHttpClients, get_external_http_clients
from rag_center.modules.feedback.schemas.feedback import FeedbackData, FeedbackRequest
from rag_center.modules.feedback.services.feedback_service import FeedbackService
from rag_center.modules.research.schemas.research import ResearchData, ResearchRequest
from rag_center.modules.research.services.research_service import ResearchService
from rag_center.modules.retrieval.schemas.rag import RetrieveData, RetrieveRequest
from rag_center.modules.retrieval.services.rag_service import RagService
from rag_center.schemas.common import ApiResponse, success_response

router = APIRouter(prefix="/rag", tags=["rag"])


def _idempotency_manager() -> IdempotencyManager | None:
    """从活跃容器获取幂等管理器；Redis 不可用时返回 None（跳过幂等）。"""
    container = active_container.get()
    if container is None or container.redis is None:
        return None
    return IdempotencyManager(container.redis)


def _idempotency_ttl() -> int:
    container = active_container.get()
    return container.settings.idempotency_ttl_seconds if container else 86400


@router.post("/retrieve", response_model=ApiResponse[RetrieveData])
async def retrieve(
    request: RetrieveRequest,
    db: AsyncSession = Depends(ApiDependencies.session),
    tenant: TenantContext = Depends(ApiDependencies.tenant),
    http_clients: ExternalHttpClients = Depends(get_external_http_clients),
    x_idempotency_key: str | None = Header(default=None, alias="X-Idempotency-Key"),
) -> dict:
    # 幂等：相同键的重复请求直接返回缓存结果
    idem = _idempotency_manager()
    if idem and x_idempotency_key:
        cached = await idem.get(tenant.tenant_id, x_idempotency_key)
        if cached is not None:
            return cached

    data = await RagService(db, http_clients=http_clients).retrieve(
        tenant.tenant_id,
        request,
    )
    response = success_response(data.model_dump(mode="json"))

    # 成功后缓存响应，供后续相同幂等键使用
    if idem and x_idempotency_key:
        await idem.set(tenant.tenant_id, x_idempotency_key, response, ttl=_idempotency_ttl())

    return response


@router.post("/research", response_model=ApiResponse[ResearchData])
async def research(
    request: ResearchRequest,
    db: AsyncSession = Depends(ApiDependencies.session),
    tenant: TenantContext = Depends(ApiDependencies.tenant),
    http_clients: ExternalHttpClients = Depends(get_external_http_clients),
    x_eval_allow_supplement: bool | None = Header(
        default=None,
        alias="X-Eval-Allow-Supplement",
        include_in_schema=False,
    ),
    x_idempotency_key: str | None = Header(default=None, alias="X-Idempotency-Key"),
) -> dict:
    # 幂等：相同键的重复请求直接返回缓存结果
    idem = _idempotency_manager()
    if idem and x_idempotency_key:
        cached = await idem.get(tenant.tenant_id, x_idempotency_key)
        if cached is not None:
            return cached

    # The hidden switch is consumed only by the offline A/B runner. Normal API
    # clients always execute the complete bounded Research flow.
    service = ResearchService(db, http_clients=http_clients)
    if x_eval_allow_supplement is False:
        data = await service.research(tenant.tenant_id, request, allow_supplement=False)
    else:
        data = await service.research(tenant.tenant_id, request)
    response = success_response(data.model_dump(mode="json"))

    # 成功后缓存响应，供后续相同幂等键使用
    if idem and x_idempotency_key:
        await idem.set(tenant.tenant_id, x_idempotency_key, response, ttl=_idempotency_ttl())

    return response


@router.post("/feedback", response_model=ApiResponse[FeedbackData])
async def submit_feedback(
    request: FeedbackRequest,
    db: AsyncSession = Depends(ApiDependencies.session),
    tenant: TenantContext = Depends(ApiDependencies.tenant),
) -> dict:
    data = await asyncio.to_thread(FeedbackService(None).submit_feedback, tenant.tenant_id, request)
    return success_response(data.model_dump(mode="json"))
