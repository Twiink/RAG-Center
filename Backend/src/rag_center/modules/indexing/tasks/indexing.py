from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from celery import shared_task
from celery.signals import worker_process_shutdown

from rag_center.core.config import load_settings, settings_context
from rag_center.core.container import ResourceContainer, active_container
from rag_center.core.logging import configure_logging, job_id_var
from rag_center.modules.indexing.services.task_dispatch import run_index_job

_WORKER_CONTAINER: ResourceContainer | None = None
_WORKER_LOOP: asyncio.AbstractEventLoop | None = None


def get_worker_loop() -> asyncio.AbstractEventLoop:
    """prefork 后在子进程内建立唯一持久事件循环。"""
    global _WORKER_LOOP
    if _WORKER_LOOP is None:
        _WORKER_LOOP = asyncio.new_event_loop()
        _WORKER_LOOP.set_default_executor(
            ThreadPoolExecutor(max_workers=load_settings().parser_max_concurrency)
        )
    return _WORKER_LOOP


def get_worker_container() -> ResourceContainer:
    global _WORKER_CONTAINER
    if _WORKER_CONTAINER is None:
        settings = load_settings()
        configure_logging(settings)
        _WORKER_CONTAINER = ResourceContainer.create(settings)
    return _WORKER_CONTAINER


async def close_worker_container() -> None:
    global _WORKER_CONTAINER
    if _WORKER_CONTAINER is not None:
        container, _WORKER_CONTAINER = _WORKER_CONTAINER, None
        await container.aclose()


@worker_process_shutdown.connect
def shutdown_worker(**kwargs) -> None:
    global _WORKER_LOOP
    if _WORKER_LOOP is not None:
        _WORKER_LOOP.run_until_complete(close_worker_container())
        _WORKER_LOOP.close()
        _WORKER_LOOP = None


@asynccontextmanager
async def worker_resources() -> AsyncGenerator[ResourceContainer]:
    yield get_worker_container()


@shared_task(name="rag_center.index_document", acks_late=True, reject_on_worker_lost=True)
def index_document_task(job_id: str) -> str:
    return get_worker_loop().run_until_complete(_run_job(job_id))


async def _run_job(job_id: str) -> str:
    container = get_worker_container()
    token = job_id_var.set(job_id)
    container_token = active_container.set(container)
    try:
        with settings_context(container.settings):
            async with container.session_factory() as session:
                return str(
                    await run_index_job(session, job_id, container.settings, container=container)
                )
    finally:
        active_container.reset(container_token)
        job_id_var.reset(token)
