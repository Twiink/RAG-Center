# syntax=docker/dockerfile:1.7
FROM python:3.12.14-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.22 /uv /uvx /usr/local/bin/
ENV UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY Backend/pyproject.toml Backend/uv.lock ./
ARG RAG_EXTRAS=""
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project ${RAG_EXTRAS:+--extra "$RAG_EXTRAS"}
COPY Backend/src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable ${RAG_EXTRAS:+--extra "$RAG_EXTRAS"} && uv build --wheel

FROM python:3.12.14-slim-bookworm AS runtime
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    RAG_APP_ENV=production RAG_DOCUMENT_STORAGE_PATH=/app/data/uploads RAG_LOG_DIR=/app/logs
RUN groupadd --gid 10001 rag && useradd --uid 10001 --gid rag --no-create-home rag
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
RUN mkdir -p /app/data/uploads /app/logs && chown -R rag:rag /app/data /app/logs
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=10s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=8)"
CMD ["rag-center", "serve"]
