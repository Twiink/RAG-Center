from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from rag_center.api.v1.deps import ApiDependencies
from rag_center.core.auth import TenantContext
from rag_center.modules.tenants.schemas.auth import AuthMeData
from rag_center.modules.tenants.services.auth_me_service import AuthMeService
from rag_center.schemas.common import ApiResponse, success_response

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me", response_model=ApiResponse[AuthMeData])
async def auth_me(
    tenant: TenantContext = Depends(ApiDependencies.tenant),
    db: AsyncSession = Depends(ApiDependencies.session),
) -> dict:
    data = await AuthMeService(db).get_me_data(tenant)
    return success_response(data.model_dump(mode="json"))
