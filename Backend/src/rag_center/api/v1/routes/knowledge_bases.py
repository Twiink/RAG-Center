from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from rag_center.api.v1.deps import ApiDependencies
from rag_center.core.auth import TenantContext
from rag_center.modules.indexing.schemas.jobs import (
    ActivateIndexVersionRequest,
    ActiveIndexVersionData,
)
from rag_center.modules.indexing.services.index_version_service import IndexVersionService
from rag_center.modules.knowledge_bases.schemas.knowledge_base import (
    CreateKnowledgeBaseRequest,
    IndexVersionData,
    KnowledgeBaseData,
    KnowledgeBaseDetailData,
    RebuildIndexData,
    TenantTreeItem,
    UpdateKnowledgeBaseRequest,
)
from rag_center.modules.knowledge_bases.services.knowledge_base_service import KnowledgeBaseService
from rag_center.schemas.common import ApiResponse, accepted_response, success_response

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge-bases"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ApiResponse[KnowledgeBaseData])
async def create_knowledge_base(
    request: CreateKnowledgeBaseRequest,
    db: AsyncSession = Depends(ApiDependencies.session),
    tenant: TenantContext = Depends(ApiDependencies.tenant),
) -> dict:
    data = await KnowledgeBaseService(db).create(tenant.tenant_id, request)
    return success_response(data.model_dump(mode="json"))


@router.get("/tree", response_model=ApiResponse[list[TenantTreeItem]])
async def get_knowledge_base_tree(
    keyword: str | None = Query(default=None),
    db: AsyncSession = Depends(ApiDependencies.session),
    tenant: TenantContext = Depends(ApiDependencies.tenant),
) -> dict:
    data = await KnowledgeBaseService(db).get_tree(tenant.tenant_id, keyword)
    return success_response([item.model_dump(mode="json") for item in data])


@router.get("/{kb_id}/index-versions", response_model=ApiResponse[list[IndexVersionData]])
async def list_index_versions(
    kb_id: str,
    db: AsyncSession = Depends(ApiDependencies.session),
    tenant: TenantContext = Depends(ApiDependencies.tenant),
) -> dict:
    data = await IndexVersionService(db).list_versions(tenant.tenant_id, kb_id)
    return success_response([item.model_dump(mode="json") for item in data])


@router.post(
    "/{kb_id}/index-versions",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ApiResponse[RebuildIndexData],
)
async def build_index_version(
    kb_id: str,
    db: AsyncSession = Depends(ApiDependencies.session),
    tenant: TenantContext = Depends(ApiDependencies.tenant),
) -> dict:
    data = await IndexVersionService(db).queue_rebuild(tenant.tenant_id, kb_id)
    return accepted_response(data.model_dump(mode="json"))


@router.get("/{kb_id}/active-index-version", response_model=ApiResponse[ActiveIndexVersionData])
async def get_active_index_version(
    kb_id: str,
    db: AsyncSession = Depends(ApiDependencies.session),
    tenant: TenantContext = Depends(ApiDependencies.tenant),
) -> dict:
    data = await KnowledgeBaseService(db).get_detail(tenant.tenant_id, kb_id)
    return success_response(
        {"kb_id": data.kb_id, "active_index_version": data.active_index_version}
    )


@router.put("/{kb_id}/active-index-version", response_model=ApiResponse[IndexVersionData])
async def activate_index_version(
    kb_id: str,
    request: ActivateIndexVersionRequest,
    db: AsyncSession = Depends(ApiDependencies.session),
    tenant: TenantContext = Depends(ApiDependencies.tenant),
) -> dict:
    data = await IndexVersionService(db).activate(
        tenant.tenant_id,
        kb_id,
        request.version,
    )
    return success_response(data.model_dump(mode="json"))


@router.delete("/{kb_id}/index-versions/{version}", status_code=204, response_class=Response)
async def delete_index_version(
    kb_id: str,
    version: str,
    db: AsyncSession = Depends(ApiDependencies.session),
    tenant: TenantContext = Depends(ApiDependencies.tenant),
) -> Response:
    await IndexVersionService(db).delete_version(tenant.tenant_id, kb_id, version)
    return Response(status_code=204)


@router.get("/{kb_id}", response_model=ApiResponse[KnowledgeBaseDetailData])
async def get_knowledge_base(
    kb_id: str,
    db: AsyncSession = Depends(ApiDependencies.session),
    tenant: TenantContext = Depends(ApiDependencies.tenant),
) -> dict:
    data = await KnowledgeBaseService(db).get_detail(tenant.tenant_id, kb_id)
    return success_response(data.model_dump(mode="json"))


@router.patch("/{kb_id}", response_model=ApiResponse[KnowledgeBaseDetailData])
async def update_knowledge_base(
    kb_id: str,
    request: UpdateKnowledgeBaseRequest,
    db: AsyncSession = Depends(ApiDependencies.session),
    tenant: TenantContext = Depends(ApiDependencies.tenant),
) -> dict:
    data = await KnowledgeBaseService(db).update(tenant.tenant_id, kb_id, request)
    return success_response(data.model_dump(mode="json"))


@router.delete("/{kb_id}", status_code=204, response_class=Response)
async def delete_knowledge_base(
    kb_id: str,
    db: AsyncSession = Depends(ApiDependencies.session),
    tenant: TenantContext = Depends(ApiDependencies.tenant),
) -> Response:
    await KnowledgeBaseService(db).delete(tenant.tenant_id, kb_id)
    return Response(status_code=204)
