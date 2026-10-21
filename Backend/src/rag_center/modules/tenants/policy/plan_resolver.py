from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from rag_center.modules.tenants.models.tenant import Tenant
from rag_center.modules.tenants.policy.plan_presets import (
    PLAN_PRESETS,
    VALID_PLANS,
    PlanFeatures,
    PlanLimits,
    PlanName,
)


@dataclass(frozen=True, slots=True)
class PlanContext:
    plan: PlanName
    features: PlanFeatures
    limits: PlanLimits


class PlanResolver:
    @staticmethod
    def resolve_plan(tenant: Tenant) -> PlanContext:
        plan = cast(PlanName, tenant.plan if tenant.plan in VALID_PLANS else "free")
        preset = PLAN_PRESETS[plan]  # type: ignore[index]
        return PlanContext(plan=plan, features=preset.features, limits=preset.limits)
