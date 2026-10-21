from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from rag_center.core.exceptions import AppError, ErrorCode
from rag_center.modules.documents.repositories.document_repository import DocumentRepository
from rag_center.modules.knowledge_bases.repositories.knowledge_base_repository import (
    KnowledgeBaseRepository,
)
from rag_center.modules.tenants.policy.plan_presets import PlanName
from rag_center.modules.tenants.policy.plan_resolver import PlanContext, PlanResolver
from rag_center.modules.tenants.repositories.tenant_repository import TenantRepository

_KB_QUOTA_MSG: dict[PlanName, str] = {
    "free": "免费档仅支持 1 个知识库",
    "standard": "标准档最多支持 5 个知识库",
    "pro": "专业档最多支持 50 个知识库",
}


class QuotaService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.tenant_repository = TenantRepository(db)
        self.knowledge_base_repository = KnowledgeBaseRepository(db)
        self.document_repository = DocumentRepository(db)

    async def resolve_plan(self, tenant_id: str) -> PlanContext:
        tenant = await self.tenant_repository.get_by_id(tenant_id)
        if tenant is None:
            raise AppError(
                ErrorCode.NOT_FOUND,
                msg="租户不存在",
                context={"tenant_id": tenant_id},
            )
        return PlanResolver.resolve_plan(tenant)

    async def check_create_kb(self, tenant_id: str) -> None:
        plan_context = await self.resolve_plan(tenant_id)
        kb_count = await self.knowledge_base_repository.count_by_tenant(tenant_id)
        if kb_count >= plan_context.limits.max_kb:
            msg = _KB_QUOTA_MSG.get(plan_context.plan, "知识库数量已达套餐上限")
            raise AppError(
                ErrorCode.QUOTA_EXCEEDED,
                msg=msg,
                context={
                    "tenant_id": tenant_id,
                    "plan": plan_context.plan,
                    "max_kb": plan_context.limits.max_kb,
                    "current_kb": kb_count,
                },
            )

    async def check_upload_document(self, tenant_id: str, kb_id: str) -> None:
        plan_context = await self.resolve_plan(tenant_id)
        doc_count = await self.document_repository.count_by_kb_id(kb_id)
        if doc_count >= plan_context.limits.max_documents_per_kb:
            raise AppError(
                ErrorCode.QUOTA_EXCEEDED,
                msg=f"单库文档数已达上限（{plan_context.limits.max_documents_per_kb}）",
                context={
                    "tenant_id": tenant_id,
                    "kb_id": kb_id,
                    "max_documents_per_kb": plan_context.limits.max_documents_per_kb,
                    "current_documents": doc_count,
                },
            )
        await self._check_processing_quota(tenant_id, plan_context)

    async def check_reindex(self, tenant_id: str) -> None:
        plan_context = await self.resolve_plan(tenant_id)
        await self._check_processing_quota(tenant_id, plan_context)

    async def _check_processing_quota(self, tenant_id: str, plan_context: PlanContext) -> None:
        processing_count = await self.document_repository.count_processing_by_tenant(tenant_id)
        if processing_count >= plan_context.limits.max_processing_documents:
            raise AppError(
                ErrorCode.QUOTA_EXCEEDED,
                msg=f"并发索引任务已达上限（{plan_context.limits.max_processing_documents}）",
                context={
                    "tenant_id": tenant_id,
                    "plan": plan_context.plan,
                    "max_processing_documents": plan_context.limits.max_processing_documents,
                    "current_processing": processing_count,
                },
            )
