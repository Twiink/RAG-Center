# 工程结论待同步

- 所属项目：`github.com-ai-moonlight-rag-center`
- 核验日期：2026-10-21
- 状态：尚未同步至 OpenViking。
- 原因：共享项目配置已固定 peer 和 actor 召回范围，但当前手动 MCP 连接的实际项目写入绑定尚未确认。不能仅凭配置文本或记忆正文标注确定服务端路由；本文件保留项目结论，不转存用户级空间。
- 同步条件：项目专属连接完成身份核验后，按以下独立主题查重，再通过真实记忆工具保存。先读取当前代码及验证记录，避免把已变化的工作区状态视作永久事实。

## 包、配置与异步资源

- 正式包为 `Backend/src/rag_center`；Python 3.12.14、uv 0.12.22、uv_build 与 uv.lock 统一开发、CI 和镜像。评测、基础 YAML、Alembic 配置及迁移进入 wheel。
- `core/config.py` 实现默认值、包内 YAML、环境 YAML、私有 YAML、.env、环境变量、显式覆盖的优先级；支持 RAG_ 分组配置、SecretStr、脱敏值和来源说明。向量维度为迁移定义的 1536。
- `main.py:create_app` 支持 Settings 注入；lifespan 和 `core/container.py` 管理 AsyncEngine、AsyncSession factory、redis.asyncio、HTTP、可选 Langfuse。观测关闭失败时仍释放其余连接池；阻塞 flush/shutdown 进入线程池。
- `infrastructure/db/session.py` 的 psycopg 异步连接使用 `run_async(register_vector_async)` 注册 pgvector，已通过真实数据库验证。Research 分支独立会话并传递知识库 DTO；模型调用前释放读取事务。

## 索引任务、迁移与恢复

- `modules/indexing/models/index_job.py` 定义 IndexJob、IndexOutbox；0012 建立可靠任务与 outbox，0013 增加 available_at。0012 的 down_revision 已修正为实际的 `0011_default_index_version_v2`，空库升级到 head 已通过。
- 文档或版本变更、任务、outbox 同事务提交；调度器 SKIP LOCKED 抢占，消息只含 job_id。`celery_app.py:publish_index_job` 必须使用明确的 Celery 应用 send_task；线程池中的 shared_task 代理可能解析到默认应用并投递错误 broker。
- `services/job_executor.py` 通过数据库 claim、独立会话 heartbeat、有效租约校验和事务发布实现至少一次投递。queued/running/succeeded/failed 统一状态，暂时错误总尝试最多三次并指数退避，永久错误立即失败。失效执行器不能续租或发布；回收已结束 runner 时吞掉已处理的租约失效异常。
- 每次尝试隔离代次，块 ID 稳定；PostgreSQL 的块记录为已发布状态真源。BM25 二次过滤未发布 ID。`services/artifacts.py` 清理过期孤儿，同时保留有效租约的准备中产物；先查询运行代次、再查询已发布指针，避免并发提交期间误删。
- 升级前没有任务的 processing 文档/building 版本由 legacy_recovery 幂等补建。prefork 子进程惰性建立持久事件循环和容器，每个任务独立会话；真实进程强制中断后可通过租约恢复，连续第二任务可复用连接池。
- 知识库行锁协调构建、文档修改及激活。processing 文档阻止创建构建快照；building 版本阻止文档修改；并发激活后只保留一个 active 并与当前版本指针一致。

## 接口、前端与质量入口

- `/api/v1` 下创建知识库返回 201，上传文档、重新索引和构建版本返回 202 并含 job_id，激活使用 PUT，删除返回 204，GET index-jobs 查询终态。响应使用 code/msg/data/request_id，认证租户来自已验证凭据。OpenAPI 的 401/409/422/5xx 等错误响应统一为 ApiErrorResponse，前端 Axios 错误类型由该契约生成。
- `Frontend/openapi.json` 为后端导出契约，`src/types/openapi.d.ts` 由 openapi-typescript 生成。前端响应类型从契约派生；Provider 的扩展统计 JSON 仅保留界面需要的可选视图字段。非 2xx、204、上传和任务终态轮询已适配。
- `make check` 包括 Ruff 格式、lint、完整正式包 ty 和单元测试。日志参数数量纳入 Ruff PLE1205/PLE1206，避免错误日志破坏降级路径。表格分块和 quality 能力开关相关测试已通过。
- Docker 使用锁依赖、多阶段构建和非 root 用户；Compose 共享上传卷，独立迁移、API、Worker、调度器。CI 先完成独立集成测试，再启动业务消费者，避免抢占测试任务；等待 API 就绪后通过真实任务验证消费者。

## 验证边界与依据

- 当前工作区已通过 236 个单元测试、17 个真实基础设施集成测试，以及前端 lint、类型检查和构建。模型使用本地协议桩，未调用付费模型。
- 已验证 PostgreSQL 16/pgvector 空库迁移、Redis 7.4、Elasticsearch 8.17.10/IK、源码外 wheel 安装和非 root Docker 构建。Compose 冒烟包含文本与 Markdown 文件上传、真实调度器及 prefork Worker 连续执行、共享上传卷读取、混合检索和 204 删除。
- GitHub 远程 CI 与可选 Langfuse 服务未在远程环境执行；本地验收记录见 `docs/validation.md`。本文件不证明任何生产部署状态。
- 正式依据：`docs/architecture.md`、`docs/configuration.md`、`docs/development.md`、`docs/api.md`、`docs/operations.md` 及当前工作区对应实现。

## Git hooks 工程约定（2026-10-21）

- 类型：项目约定
- 关键位置：`.githooks/`、`scripts/git-hooks/common.sh`、`scripts/install-git-hooks.sh`
- 安装方式：`sh scripts/install-git-hooks.sh`，设置 `core.hooksPath=.githooks`
- pre-commit：仅检查暂存的 `Backend` Python 文件，拒绝同一文件部分暂存
- commit-msg：要求 `<type>(<scope>): <subject>`，主题包含中文且仅一行
- pre-push：校验 `Backend/uv.lock` 并运行 Ruff
- post-merge：hook 变更自动刷新，依赖变更提示同步
- pre-rebase：保护 `main`、`master`、`release/*`；逃生开关为 `RAG_CENTER_ALLOW_PROTECTED_REBASE`
- 验证结果：5 个 hook 安装成功；commit-msg 通过合法/非法格式测试；pre-push 实际通过；pre-rebase 保护分支测试通过

## 观测基础设施完善（2026-10-21）

**类型**: 架构完善
**适用范围**: 全栈观测能力
**关键位置**:
- `Backend/src/rag_center/core/logging.py`: 日志脱敏与结构化
- `Backend/src/rag_center/core/metrics.py`: Prometheus 指标体系
- `Backend/src/rag_center/core/config.py`: 新增配置项
- `Backend/src/rag_center/infrastructure/providers/llm/resilience.py`: LLM 韧性工具
- `docs/observability.md`: 观测指南
- `docs/troubleshooting.md`: 故障排查

**已验证事实**:
1. 日志脱敏覆盖 Bearer token、sk- 密钥、URL 凭据、敏感字段名，`JsonFormatter` 输出 exc_info 和 extra 字段
2. Prometheus 指标扩展：LLM（requests/duration/tokens/cost）、Embedding/Rerank、Research（runs/stage_duration）、检索失败、降级事件、限流、Worker 心跳
3. LLM 错误分类（LLM_TIMEOUT/RATE_LIMIT/TOKEN_LIMIT/NO_RESPONSE/PROVIDER_FAILED）并映射到 HTTP 状态码
4. LLM 重试机制：`retry_with_backoff` 支持指数退避，可配置 `RAG_LLM_MAX_RETRIES`（默认 2）
5. 新增单元测试 18 个（test_logging_redaction.py: 9, test_llm_resilience.py: 9），测试套件 254 passed，工程契约 8 passed

**设计决策**:
- 暂不引入 OpenTelemetry，只用 Prometheus + 结构化日志，避免增加依赖复杂度
- 指标标签严格限制低基数（model/role/status/stage），不使用 tenant_id/document_id
- 日志文件可通过 `RAG_LOG_FILE_ENABLED=false` 在容器内禁用，只输出到 console
- 服务名通过 `RAG_SERVICE_NAME` 区分 api/worker/scheduler，便于日志聚合
- LLM 重试只对可重试错误（429/5xx/timeout/连接错误）生效，token 超限不重试

**未完成项（推迟到下阶段）**:
- LLM Provider 集成重试和指标（需修改 `openai_compatible.py` 调用路径）
- Research 超时自动降级（需 `asyncio.timeout` 包装各阶段）
- CLI 诊断命令（`jobs stats`/`jobs failures`）
- 安全检查（production + auth_enabled=false 报错）
- Observability Compose（Prometheus + Grafana + Dashboard）
- Worker/Scheduler 指标暴露（需在进程启动时调用 `start_http_server`）

**验证结果**: 所有单元测试和工程契约测试通过，配置文档已更新

**依据**: 当前工作区代码及测试套件，参考 AgentTemplate 观测实践
