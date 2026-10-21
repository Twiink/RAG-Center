from __future__ import annotations

import json
from collections.abc import Callable

import typer

from rag_center.core.config import load_settings, redacted_settings

app = typer.Typer(help="RAG Center 后端工程命令")
worker_app = typer.Typer(help="索引 Worker 管理")
scheduler_app = typer.Typer(help="索引调度器管理")
app.add_typer(worker_app, name="worker")
app.add_typer(scheduler_app, name="scheduler")


@app.command("init")
def init() -> None:
    """初始化本地配置和上传目录。"""

    from pathlib import Path

    settings = load_settings()
    Path(settings.document_storage_path).mkdir(parents=True, exist_ok=True)
    typer.echo("上传目录已初始化")


config_app = typer.Typer(help="配置查看与校验")
app.add_typer(config_app, name="config")


@config_app.command("show")
def config_show() -> None:
    """查看脱敏配置。"""

    settings = load_settings()
    typer.echo(json.dumps(redacted_settings(settings), ensure_ascii=False, indent=2))


@config_app.command("validate")
def config_validate() -> None:
    """校验配置。"""

    try:
        load_settings()
    except Exception as exc:
        from pydantic import ValidationError

        message = (
            json.dumps(
                [
                    {"field": error["loc"], "message": error["msg"]}
                    for error in exc.errors(include_input=False)
                ],
                ensure_ascii=False,
            )
            if isinstance(exc, ValidationError)
            else str(exc)
        )
        typer.secho(message, fg=typer.colors.RED)
        raise typer.Exit(1) from exc
    typer.echo("配置校验通过")


def _run_check() -> None:
    import subprocess
    from pathlib import Path

    backend = Path.cwd()
    if not (backend / "pyproject.toml").exists() and (backend / "Backend/pyproject.toml").exists():
        backend = backend / "Backend"
    commands: list[tuple[str, Callable[[], int]]] = [
        (
            "format",
            lambda: subprocess.call(
                ["uv", "run", "--project", str(backend), "ruff", "format", "--check", str(backend)],
                cwd=backend,
            ),
        ),
        (
            "lint",
            lambda: subprocess.call(
                ["uv", "run", "--project", str(backend), "ruff", "check", str(backend)], cwd=backend
            ),
        ),
        (
            "type",
            lambda: subprocess.call(
                ["uv", "run", "--project", str(backend), "ty", "check"], cwd=backend
            ),
        ),
        (
            "tests",
            lambda: subprocess.call(
                ["uv", "run", "--project", str(backend), "pytest"], cwd=backend
            ),
        ),
    ]
    for name, command in commands:
        typer.echo(f"正在执行 {name}")
        if command() != 0:
            raise typer.Exit(1)


@app.command("check")
def check() -> None:
    """运行格式、lint、类型和单元测试。"""

    _run_check()


@app.command("dev")
def dev(host: str = "127.0.0.1", port: int = 8000, reload: bool = True) -> None:
    """启动开发服务。"""

    import uvicorn

    uvicorn.run("rag_center.main:app", host=host, port=port, reload=reload)


@app.command("serve")
def serve(host: str = "0.0.0.0", port: int = 8000, workers: int = 1) -> None:
    """启动生产服务。"""

    import uvicorn

    uvicorn.run("rag_center.main:app", host=host, port=port, workers=workers)


@app.command("migrate")
def migrate(revision: str = "head") -> None:
    """执行数据库迁移。"""

    from alembic import command

    from rag_center.cli.paths import migration_config

    config = migration_config()
    command.upgrade(config, revision)


@app.command("preflight")
def preflight() -> None:
    """只读启动预检。"""

    from rag_center.core.diagnostics import preflight

    report = preflight()
    typer.echo(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    if report["status"] != "ok":
        raise typer.Exit(1)


@app.command("doctor")
def doctor() -> None:
    """主动探测外部依赖。"""

    from rag_center.core.diagnostics import doctor

    report = doctor()
    typer.echo(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    if report["status"] != "ok":
        raise typer.Exit(1)


@worker_app.command("run")
def worker_run(concurrency: int = 2, queue: str = "indexing") -> None:
    """以 prefork 模式启动 Celery Worker。"""

    from rag_center.celery_app import celery_app

    celery_app.worker_main(
        [
            "worker",
            "--concurrency",
            str(concurrency),
            "--queues",
            queue,
            "--pool",
            "prefork",
            "--loglevel",
            "INFO",
        ]
    )


@scheduler_app.command("run")
def scheduler_run() -> None:
    """启动 outbox 调度器。"""

    import asyncio

    from rag_center.celery_app import publish_index_job
    from rag_center.core.container import ResourceContainer
    from rag_center.core.logging import configure_logging
    from rag_center.modules.indexing.services.scheduler import OutboxScheduler

    settings = load_settings()
    configure_logging(settings)
    container = ResourceContainer.create(settings)
    scheduler = OutboxScheduler(
        container.session_factory, settings, publish_index_job, container=container
    )

    async def run() -> None:
        try:
            await scheduler.run_forever()
        finally:
            await container.aclose()

    asyncio.run(run())


from rag_center.cli.management import index_app, key_app, tenant_app

app.add_typer(tenant_app, name="tenant")
app.add_typer(key_app, name="key")
app.add_typer(index_app, name="index")


@app.command("openapi")
def export_openapi(output: str = "openapi.json"):
    """无需外部连接导出接口契约。"""
    from pathlib import Path

    from rag_center.main import create_app

    schema = create_app().openapi()
    Path(output).write_text(
        json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


@app.command("eval", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def evaluate(ctx: typer.Context):
    """评测入口，子命令参数使用 rag-center eval --help 查看。"""
    from rag_center.evaluation.cli import main

    raise typer.Exit(main(ctx.args))
