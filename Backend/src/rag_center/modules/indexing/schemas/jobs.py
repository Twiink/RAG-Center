from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ActivateIndexVersionRequest(BaseModel):
    version: str = Field(min_length=1, max_length=32, pattern=r"^v[0-9]+$")


class ActiveIndexVersionData(BaseModel):
    kb_id: str
    active_index_version: str


class IndexJobData(BaseModel):
    job_id: str
    resource_id: str
    job_type: Literal["document", "index_version"]
    status: Literal["queued", "running", "succeeded", "failed"]
    attempt: int
    max_attempts: int
    error_message: str | None
    created_at: datetime
    updated_at: datetime
