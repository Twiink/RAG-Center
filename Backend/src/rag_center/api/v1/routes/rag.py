import asyncio

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from rag_center.api.v1.deps import ApiDependencies
from rag_center.core.auth import TenantContext
from rag_center.infrastructure.http.clients import ExternalHttpClients, get_external_http_clients
from rag_center.modules.feedback.schemas.feedback import FeedbackData, FeedbackRequest
from rag_center.modules.feedback.services.feedback_service import FeedbackService
from rag_center.modules.research.schemas.research import ResearchData, ResearchRequest
from rag_center.modules.research.services.research_service import ResearchService
from rag_center.modules.retrieval.schemas.rag import RetrieveData, RetrieveRequest
from rag_center.modules.retrieval.services.rag_service import RagService
from rag_center.schemas.common import ApiResponse, success_response

router = APIRouter(prefix="/rag", tags=["rag"])


@router.post("/retrieve", response_model=ApiResponse[RetrieveData])
async def retrieve(
    request: RetrieveRequest,
    db: AsyncSession = Depends(ApiDependencies.session),
    tenant: TenantContext = Depends(ApiDependencies.tenant),
    http_clients: ExternalHttpClients = Depends(get_external_http_clients),
) -> dict:
    data = await RagService(db, http_clients=http_clients).retrieve(
        tenant.tenant_id,
        request,
    )
    return success_response(data.model_dump(mode="json"))


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
) -> dict:
    # The hidden switch is consumed only by the offline A/B runner. Normal API
    # clients always execute the complete bounded Research flow.
    service = ResearchService(db, http_clients=http_clients)
    if x_eval_allow_supplement is False:
        data = await service.research(tenant.tenant_id, request, allow_supplement=False)
    else:
        data = await service.research(tenant.tenant_id, request)
    return success_response(data.model_dump(mode="json"))


@router.post("/feedback", response_model=ApiResponse[FeedbackData])
async def submit_feedback(
    request: FeedbackRequest,
    db: AsyncSession = Depends(ApiDependencies.session),
    tenant: TenantContext = Depends(ApiDependencies.tenant),
) -> dict:
    data = await asyncio.to_thread(FeedbackService(None).submit_feedback, tenant.tenant_id, request)
    return success_response(data.model_dump(mode="json"))
