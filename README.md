# RAG-Center

基于 FastAPI、PostgreSQL/pgvector、Redis/Celery 和 Elasticsearch 的异步模块化 RAG 后端，配套 React 管理界面。支持多租户知识库、文本及 PDF/DOCX 上传、向量与关键词混合检索、受控 Research 和反馈。

## 本地开发

开发、CI 和镜像统一使用 Python **3.12.14**、uv **0.12.22**，依赖由 `Backend/uv.lock` 锁定。前端使用 Node.js 22 和 `npm ci`。

```bash
make init
sh scripts/install-git-hooks.sh
cp Backend/.env.example Backend/.env
# 填写自己的模型服务配置；配置只保存在本机

docker compose up -d postgres redis elasticsearch
make migrate
uv run --directory Backend rag-center tenant create tenant_demo demo --plan pro
mkdir -p .data
uv run --directory Backend rag-center key create tenant_demo local --output ../.data/local.key
make doctor
make dev
```

Worker 和调度器分别在新终端运行 `make worker`、`make scheduler`。前端在 `Frontend/.env` 配置 `API_KEY` 后运行：

```bash
cd Frontend
npm ci
npm run dev
```

浏览器打开 `http://localhost:5173`，API 文档在 `http://localhost:8000/docs`。前端内嵌的 Key 只适用于可信本地管理界面；共享部署应由身份网关提供用户凭据。

## Docker Compose

```bash
# 先通过环境变量配置 RAG_MODEL_API_KEY、RAG_LLM_API_KEY 及相应 BASE_URL
make compose-up
docker compose exec api rag-center tenant create tenant_demo demo --plan pro
# 创建 Key 时把凭据写到容器内的受保护文件；不要写进镜像或 Git

docker compose exec api rag-center doctor
```

Compose 启动独立迁移任务、API、prefork Worker、调度器以及基础设施。API 与 Worker 共享上传卷。Langfuse 按需使用 `docker compose --profile langfuse up -d`，后端镜像启用 SDK 需构建 `--build-arg RAG_EXTRAS=langfuse` 并配置对应 `RAG_LANGFUSE_*`。

## 质量入口

```bash
make check             # Ruff 格式、lint、完整包 ty、单元测试
make frontend-check    # ESLint、TypeScript、构建
make openapi           # 后端契约导出并生成前端类型
uv build --project Backend --wheel
```

集成测试独立运行，需要显式指定以 `_test` 结尾的隔离数据库：

```bash
RAG_TEST_DATABASE_URL='postgresql+psycopg://rag:rag@127.0.0.1:15432/rag_center_test' \
RAG_TEST_REDIS_URL='redis://127.0.0.1:16379/0' \
RAG_TEST_ELASTICSEARCH_URL='http://127.0.0.1:19200' make integration
```

测试使用本地模型协议桩。GitHub CI 另外验证 wheel 安装、空库迁移、Docker 构建和真实 Compose 任务链路。

## 工程文档

- [架构与扩展](docs/architecture.md)
- [配置与来源](docs/configuration.md)
- [开发与测试](docs/development.md)
- [API 契约](docs/api.md)
- [部署、升级与任务恢复](docs/operations.md)
- [本地验收记录](docs/validation.md)

后端位于 `Backend/src/rag_center`，前端位于 `Frontend`。工程约定和项目记忆身份见 [AGENTS.md](AGENTS.md)。
