# 工程化重构验收记录

核验日期：2026-10-21。对象为当前工作区的 `Backend`、`Frontend`、Docker/Compose、CLI 和 GitHub Actions 配置。测试使用独立数据库及本地模型协议桩。

## 验收结果

| 入口或场景 | 结果 |
| --- | --- |
| `make check` | Ruff 格式、lint、完整正式包 ty 通过；236 个单元测试通过，集成测试独立运行 |
| `make frontend-check` | ESLint、TypeScript、Vite 构建通过 |
| `make openapi` | 后端导出与 openapi-typescript 生成通过；源码导出、前端契约及 wheel 导出一致 |
| PostgreSQL 16/pgvector 空库迁移 | 0001 至 `0013_index_job_delivery` 全链路通过 |
| 独立集成测试 | 17 个场景通过；使用 PostgreSQL 16/pgvector、Redis 7.4、Elasticsearch 8.17.10/IK |
| `uv build --project Backend --wheel` | wheel 构建通过；配置、迁移及评测模块随包分发 |
| 源码外 wheel 安装 | 在全新虚拟环境安装锁定运行依赖与 wheel；从 `/tmp` 校验配置、只读 preflight、导出 OpenAPI 和导入位置，通过 |
| Docker | 多阶段锁定构建通过；运行身份为 UID/GID 10001 |
| Compose | 独立迁移、API、Worker、调度器及模型桩启动；API 就绪检查通过 |
| 容器内 `rag-center doctor` | 存储权限、数据库、迁移、pgvector、向量维度、Redis、Elasticsearch 检查均通过 |
| Compose 业务冒烟 | 文本及 Markdown 文件上传、两个连续索引任务、混合检索与 204 删除通过；验证共享存储及真实 prefork 资源复用 |

## 集成覆盖

- 上传文本、上传文件、索引发布，以及 vector、BM25、hybrid 检索。
- 重建期间保留已发布分块、活动任务冲突、租户隔离、HTTP 参数及状态码。
- 构建新索引版本、构建冲突、文档修改限制、并发激活、归档版本删除。
- 未发布 Elasticsearch 产物过滤、有效租约代次保护、过期孤儿清理。
- 重复消息并发 claim、指数退避、Redis broker 中断与补投、总尝试耗尽和永久失败。
- 过期执行器不能替换新代次，租约失效不能复活，heartbeat 使用独立会话。
- 历史 processing 文档恢复、Research 并发分支会话隔离。
- 真实 prefork Worker 进程组强制中断、租约回收、新 Worker 恢复，以及连续第二任务。
- 调度器在线程池中使用明确的 Celery 应用发布，消息仅含 job_id。
- 关闭 Elasticsearch 时索引可完成，hybrid 按降级规则返回向量结果。

## 复现

```bash
make init
make check
cd Frontend && npm ci && cd ..
make frontend-check
make openapi
uv build --project Backend --wheel
docker build -t rag-center:local .
```

完整隔离环境步骤以 `.github/workflows/ci.yml` 为准：先启动基础设施、执行迁移与集成测试，再启动 API、Worker、调度器和模型桩；仅等待 API 的健康检查，随后执行 `Backend/tests/support/compose_smoke.py` 验证消费者。`deploy/compose.ci.yaml` 只提供测试模型地址及协议桩占位凭据。

集成测试要求明确设置 `RAG_TEST_DATABASE_URL`，数据库名以 `_test` 结尾，同时设置独立的 `RAG_TEST_REDIS_URL`、`RAG_TEST_ELASTICSEARCH_URL`。不要将该步骤指向开发或生产数据。

## 验证边界

- GitHub Actions 工作流已配置，并在本地执行相应检查；尚未在 GitHub 远程触发运行。
- Langfuse 可选依赖已声明，生命周期使用桩对象验证；本次未启动可选 Langfuse 服务，也未连接付费模型服务。
- 当前依赖中的 Starlette TestClient 提示 httpx 适配弃用；单元测试通过。前端主包约 591 KB，Vite 提示后续可拆包；构建通过。
- API 契约和队列协议包含破坏性变化，前后端需要同时升级，迁移与恢复步骤见 `operations.md`。
- 本轮创建的独立测试容器及测试卷已清理；构建镜像和 wheel 保留在本机用于复查。
- OpenViking 项目写入绑定尚未确认，项目结论保存在 `engineering-memory-pending.md`，未向远程记忆写入。
