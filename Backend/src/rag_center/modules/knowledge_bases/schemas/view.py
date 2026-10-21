from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class KnowledgeBaseView:
    """检索跨会话传递的只读快照，禁止携带 ORM 会话和关系。"""

    id: str
    name: str
    description: str | None
    settings: dict
    active_index_version: str

    @classmethod
    def from_model(cls, model: Any):
        return cls(
            id=model.id,
            name=model.name,
            description=model.description,
            settings=dict(model.settings or {}),
            active_index_version=model.active_index_version,
        )
