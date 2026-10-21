from sqlalchemy.ext.asyncio import AsyncSession

from rag_center.core.auth import TenantContext
from rag_center.modules.knowledge_bases.repositories.knowledge_base_repository import (
    KnowledgeBaseRepository,
)
from rag_center.modules.tenants.policy.plan_resolver import PlanResolver
from rag_center.modules.tenants.repositories.tenant_repository import TenantRepository
from rag_center.modules.tenants.schemas.auth import AuthFeatures, AuthLimits, AuthMeData, AuthUsage
from rag_center.modules.tenants.services.rate_limit_service import get_rate_limit_service


class AuthMeService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.tenant_repository = TenantRepository(db)
        self.knowledge_base_repository = KnowledgeBaseRepository(db)

    async def get_me_data(self, tenant_ctx: TenantContext) -> AuthMeData:
        tenant = await self.tenant_repository.get_by_id(tenant_ctx.tenant_id)
        plan_context = PlanResolver.resolve_plan(tenant) if tenant is not None else None

        if plan_context is None:
            return AuthMeData(
                tenant_id=tenant_ctx.tenant_id,
                tenant_name=tenant_ctx.tenant_name,
                key_prefix=tenant_ctx.key_prefix,
                key_name=tenant_ctx.key_name,
                plan="free",
                features=AuthFeatures(
                    allowed_profiles=["speed"],
                    hybrid_allowed=False,
                    rerank_allowed=False,
                    evidence_allowed=False,
                    query_rewrite_allowed=False,
                    research_allowed=False,
                ),
                limits=AuthLimits(
                    retrieve_qps=3,
                    retrieve_daily=500,
                    max_kb=1,
                    max_kb_per_retrieve=1,
                    max_documents_per_kb=30,
                    max_processing_documents=1,
                ),
                usage=AuthUsage(kb_count=0, retrieve_daily=0),
            )

        kb_count = await self.knowledge_base_repository.count_by_tenant(tenant_ctx.tenant_id)
        retrieve_daily = await get_rate_limit_service().get_daily_retrieve_count(
            tenant_ctx.tenant_id
        )
        features = plan_context.features
        limits = plan_context.limits

        return AuthMeData(
            tenant_id=tenant_ctx.tenant_id,
            tenant_name=tenant_ctx.tenant_name,
            key_prefix=tenant_ctx.key_prefix,
            key_name=tenant_ctx.key_name,
            plan=plan_context.plan,
            features=AuthFeatures(
                allowed_profiles=sorted(features.allowed_profiles),
                hybrid_allowed=features.hybrid_allowed,
                rerank_allowed=features.rerank_allowed,
                evidence_allowed=features.evidence_allowed,
                query_rewrite_allowed=features.query_rewrite_allowed,
                research_allowed=features.research_allowed,
            ),
            limits=AuthLimits(
                retrieve_qps=limits.retrieve_qps,
                retrieve_daily=limits.daily_retrieve_limit,
                max_kb=limits.max_kb,
                max_kb_per_retrieve=limits.max_kb_per_retrieve,
                max_documents_per_kb=limits.max_documents_per_kb,
                max_processing_documents=limits.max_processing_documents,
            ),
            usage=AuthUsage(
                kb_count=kb_count,
                retrieve_daily=retrieve_daily,
            ),
        )
