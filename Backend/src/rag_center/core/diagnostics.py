from __future__ import annotations

import asyncio
import os
from pathlib import Path

from sqlalchemy import func, select, text

from rag_center.core.config import VECTOR_DIMENSIONS, Settings, load_settings
from rag_center.core.container import ResourceContainer
from rag_center.modules.indexing.models.index_job import IndexJob


def preflight(settings: Settings | None = None) -> dict:
    """只读验证配置和目录权限，不创建文件。"""
    active = settings or load_settings()
    path = Path(active.document_storage_path).resolve()
    parent = path
    while not parent.exists():
        parent = parent.parent
    writable = parent.is_dir() and os.access(parent, os.W_OK | os.X_OK)
    return {
        "status": "ok" if writable else "failed",
        "checks": {
            "config": "ok",
            "storage": "ok" if writable else "not writable",
            "storage_directory": str(path),
            "vector_dimensions": VECTOR_DIMENSIONS,
        },
    }


async def inspect_dependencies(container: ResourceContainer) -> dict:
    """有超时的只读依赖检查，输出只包含状态或错误类型，不回显连接串。"""
    checks = dict(preflight(container.settings)["checks"])
    try:
        async with asyncio.timeout(5):
            async with container.session_factory() as session:
                await session.execute(text("SELECT 1"))
                checks["database"] = "ok"
                extension = await session.scalar(
                    text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
                )
                checks["pgvector"] = "ok" if extension else "missing"
                dimensions = await session.scalar(
                    text(
                        "SELECT format_type(atttypid, atttypmod) FROM pg_attribute WHERE attrelid = to_regclass('chunks') AND attname = 'embedding' AND NOT attisdropped"
                    )
                )
                checks["vector_schema"] = (
                    "ok" if dimensions == f"vector({VECTOR_DIMENSIONS})" else "mismatch"
                )
                from alembic.script import ScriptDirectory

                from rag_center.cli.paths import migration_config

                revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
                checks["migration"] = (
                    "ok"
                    if revision
                    == ScriptDirectory.from_config(migration_config()).get_current_head()
                    else "outdated"
                )
                rows = (
                    await session.execute(
                        select(IndexJob.status, func.count()).group_by(IndexJob.status)
                    )
                ).all()
                checks["index_jobs"] = {status: count for status, count in rows}
                for status, count in rows:
                    container.metrics.queue.labels(status).set(count)
    except Exception as exc:
        checks["database"] = type(exc).__name__
    try:
        if container.redis is None:
            raise RuntimeError("Redis 未初始化")
        await container.redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = type(exc).__name__
    if (
        container.settings.elasticsearch_enabled
        and container.settings.keyword_search_provider == "elasticsearch"
    ):
        try:
            if container.external_clients is None:
                raise RuntimeError("HTTP 客户端未初始化")
            response = await container.external_clients.elasticsearch.get(
                f"{container.settings.elasticsearch_url.rstrip('/')}/_cluster/health", timeout=5
            )
            response.raise_for_status()
            checks["elasticsearch"] = (
                "ok" if response.json().get("status") in {"green", "yellow"} else "unavailable"
            )
        except Exception as exc:
            checks["elasticsearch"] = type(exc).__name__
    failed = any(
        value != "ok"
        for key, value in checks.items()
        if key not in {"storage_directory", "vector_dimensions", "index_jobs"}
    )
    return {"status": "failed" if failed else "ok", "checks": checks}


def doctor(settings: Settings | None = None) -> dict:
    async def run():
        container = ResourceContainer.create(settings or load_settings())
        try:
            return await inspect_dependencies(container)
        finally:
            await container.aclose()

    return asyncio.run(run())
