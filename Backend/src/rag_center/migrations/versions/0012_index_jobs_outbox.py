"""Add reliable index jobs and transactional outbox.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011_default_index_version_v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "index_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=False),
        sa.Column("job_type", sa.String(length=32), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_index_jobs_tenant_id", "index_jobs", ["tenant_id"])
    op.create_index("ix_index_jobs_resource_id", "index_jobs", ["resource_id"])
    op.create_index("ix_index_jobs_status", "index_jobs", ["status"])
    op.create_index(
        "ix_index_jobs_active_lookup",
        "index_jobs",
        ["resource_id", "job_type", "status"],
    )
    op.create_index(
        "uq_index_jobs_active_resource",
        "index_jobs",
        ["resource_id", "job_type"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )

    op.create_table(
        "index_outbox",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("topic", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["job_id"], ["index_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_index_outbox_job_id", "index_outbox", ["job_id"])
    op.create_index(
        "ix_index_outbox_pending",
        "index_outbox",
        ["id"],
        postgresql_where=sa.text("published_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_index_jobs_active_resource", table_name="index_jobs")
    op.drop_index("ix_index_outbox_pending", table_name="index_outbox")
    op.drop_index("ix_index_outbox_job_id", table_name="index_outbox")
    op.drop_table("index_outbox")
    op.drop_index("ix_index_jobs_active_lookup", table_name="index_jobs")
    op.drop_index("ix_index_jobs_status", table_name="index_jobs")
    op.drop_index("ix_index_jobs_resource_id", table_name="index_jobs")
    op.drop_index("ix_index_jobs_tenant_id", table_name="index_jobs")
    op.drop_table("index_jobs")
