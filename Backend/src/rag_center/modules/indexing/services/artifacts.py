"""清理未发布和已替换的 Elasticsearch 代次，PostgreSQL 是发布状态真源。"""

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from rag_center.modules.indexing.models.chunk import Chunk
from rag_center.modules.indexing.models.index_job import IndexJob


async def cleanup_index_artifacts(container, cursor=None):
    if (
        not container.settings.elasticsearch_enabled
        or container.settings.keyword_search_provider != "elasticsearch"
    ):
        return None
    client = container.external_clients.elasticsearch
    url = f"{container.settings.elasticsearch_url.rstrip('/')}/{container.settings.elasticsearch_index}"
    query = {
        "size": 100,
        "sort": [{"created_at": "asc"}, {"chunk_id": "asc"}],
        "query": {
            "range": {
                "created_at": {
                    "lt": (
                        datetime.now(UTC)
                        - timedelta(seconds=container.settings.index_job_lease_seconds * 2)
                    ).isoformat()
                }
            }
        },
        "_source": ["chunk_id", "metadata"],
    }
    if cursor:
        query["search_after"] = cursor
    response = await client.post(f"{url}/_search", json=query, timeout=5)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    hits = response.json().get("hits", {}).get("hits", [])
    if not hits:
        return None
    candidates = [
        hit for hit in hits if hit.get("_source", {}).get("metadata", {}).get("generation")
    ]
    async with container.session_factory() as session:
        # 先读有效任务，再读发布指针：发布若在两次查询间提交，仍至少被其中一次保护。
        # 反向查询会把刚从 running 变为 succeeded 的已发布代次误当成孤儿。
        active_generations = {
            f"{job_id}:{attempt}"
            for job_id, attempt in (
                await session.execute(
                    select(IndexJob.id, IndexJob.attempt).where(
                        IndexJob.id.in_(
                            [
                                hit["_source"]["metadata"]["generation"].rsplit(":", 1)[0]
                                for hit in candidates
                            ]
                        ),
                        IndexJob.status == "running",
                        IndexJob.lease_expires_at > datetime.now(UTC),
                    )
                )
            ).all()
        }
        published = set(
            (
                await session.scalars(
                    select(Chunk.id).where(Chunk.id.in_([hit["_id"] for hit in candidates]))
                )
            ).all()
        )
    lines = [
        json.dumps(
            {"delete": {"_index": container.settings.elasticsearch_index, "_id": hit["_id"]}}
        )
        for hit in candidates
        if hit["_id"] not in published
        and hit["_source"]["metadata"]["generation"] not in active_generations
    ]
    if lines:
        response = await client.post(
            f"{url}/_bulk",
            content="\n".join(lines) + "\n",
            headers={"Content-Type": "application/x-ndjson"},
            timeout=10,
        )
        response.raise_for_status()
        if response.json().get("errors"):
            raise RuntimeError("清理索引代次失败")
    return hits[-1]["sort"] if len(hits) == 100 else None
