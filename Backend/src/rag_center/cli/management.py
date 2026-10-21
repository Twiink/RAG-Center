"""租户、凭据及索引运维命令；连接均由一次命令的资源容器管理。"""

import asyncio
import json
import os
from pathlib import Path

import typer
from sqlalchemy import select

from rag_center.core.config import load_settings, settings_context
from rag_center.core.container import ResourceContainer
from rag_center.infrastructure.ids import generate_uuid
from rag_center.modules.tenants.api_key_crypto import generate_api_key_material
from rag_center.modules.tenants.models.api_key import ApiKey
from rag_center.modules.tenants.models.tenant import Tenant

tenant_app = typer.Typer(help="租户管理")
key_app = typer.Typer(help="API Key 管理")
index_app = typer.Typer(help="索引管理")


def run_operation(operation):
    async def run():
        settings = load_settings()
        with settings_context(settings):
            container = ResourceContainer.create(settings)
            try:
                async with container.session_factory() as session:
                    return await operation(session)
            finally:
                await container.aclose()

    try:
        return asyncio.run(run())
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


@tenant_app.command("create")
def create_tenant(tenant_id: str, name: str, plan: str = "free"):
    if plan not in {"free", "standard", "pro"}:
        raise typer.BadParameter("plan 必须为 free、standard 或 pro")

    async def create(session):
        if await session.get(Tenant, tenant_id):
            raise ValueError("租户已存在")
        session.add(Tenant(id=tenant_id, name=name, status="active", plan=plan))
        await session.commit()

    run_operation(create)
    typer.echo("租户已创建")


@tenant_app.command("list")
def list_tenants():
    async def rows(session):
        return [
            {"id": item.id, "name": item.name, "plan": item.plan, "status": item.status}
            for item in (await session.scalars(select(Tenant))).all()
        ]

    typer.echo(json.dumps(run_operation(rows), ensure_ascii=False))


@key_app.command("create")
def create_key(
    tenant_id: str,
    name: str,
    output: Path = typer.Option(..., help="仅以 0600 权限创建凭据文件；禁止覆盖"),
):
    """凭据写入受保护文件，终端只显示 Key ID。"""
    raw_key, key_hash, prefix = generate_api_key_material()
    key_id = generate_uuid()

    async def create(session):
        if await session.get(Tenant, tenant_id) is None:
            raise ValueError("租户不存在")
        # 先以排他模式创建文件；数据库失败时移除本次文件。
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w") as file:
                file.write(raw_key + "\n")
            session.add(
                ApiKey(
                    id=key_id,
                    tenant_id=tenant_id,
                    name=name,
                    key_hash=key_hash,
                    key_prefix=prefix,
                    status="active",
                )
            )
            await session.commit()
        except Exception:
            await asyncio.to_thread(output.unlink, missing_ok=True)
            raise

    run_operation(create)
    typer.echo(f"Key 已创建：{key_id}；凭据文件：{output}")


@key_app.command("list")
def list_keys(tenant_id: str):
    async def rows(session):
        return [
            {"id": item.id, "name": item.name, "prefix": item.key_prefix, "status": item.status}
            for item in (
                await session.scalars(select(ApiKey).where(ApiKey.tenant_id == tenant_id))
            ).all()
        ]

    typer.echo(json.dumps(run_operation(rows), ensure_ascii=False))


@key_app.command("revoke")
def revoke_key(key_id: str):
    async def revoke(session):
        key = await session.get(ApiKey, key_id)
        if key is None:
            raise ValueError("Key 不存在")
        key.status = "revoked"
        await session.commit()

    run_operation(revoke)
    typer.echo("Key 已撤销")


@index_app.command("rebuild")
def rebuild(tenant_id: str, kb_id: str):
    from rag_center.modules.indexing.services.index_version_service import IndexVersionService

    async def queue(session):
        return (await IndexVersionService(session).queue_rebuild(tenant_id, kb_id)).model_dump()

    typer.echo(json.dumps(run_operation(queue)))


@index_app.command("reindex-document")
def reindex_document(tenant_id: str, document_id: str, reparse: bool = False):
    from rag_center.modules.documents.services.document_service import DocumentService

    async def queue(session):
        return (
            await DocumentService(session).reindex(tenant_id, document_id, reparse=reparse)
        ).model_dump()

    typer.echo(json.dumps(run_operation(queue)))
