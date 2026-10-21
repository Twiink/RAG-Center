from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from rag_center.api.v1.deps import ApiDependencies
from rag_center.core.auth import TenantContext
from rag_center.modules.indexing.schemas.jobs import IndexJobData
from rag_center.modules.indexing.services.job_service import IndexJobService
from rag_center.schemas.common import ApiResponse, success_response

router = APIRouter(prefix="/index-jobs", tags=["index-jobs"])


@router.get("/{job_id}", response_model=ApiResponse[IndexJobData])
async def get_index_job(
    job_id: str,
    db: Annotated[AsyncSession, Depends(ApiDependencies.session)],
    tenant: Annotated[TenantContext, Depends(ApiDependencies.tenant)],
) -> dict:
    data = await IndexJobService(db).get(job_id, tenant.tenant_id)
    return success_response(data.model_dump(mode="json"))
