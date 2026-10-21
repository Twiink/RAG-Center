"""为任务重试定义独立的可投递时间。"""

import sqlalchemy as sa
from alembic import op

revision = "0013_index_job_delivery"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "index_jobs",
        sa.Column(
            "available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_index_jobs_queued_available",
        "index_jobs",
        ["available_at"],
        postgresql_where=sa.text("status = 'queued'"),
    )


def downgrade():
    op.drop_index("ix_index_jobs_queued_available", "index_jobs")
    op.drop_column("index_jobs", "available_at")
