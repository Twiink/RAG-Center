# 部署、升级与任务恢复

## 部署

Dockerfile 使用 Python 3.12.14、uv 0.12.22、多阶段构建、锁定依赖和非 root 用户。项目以非 editable wheel 安装；基础 YAML、Alembic 配置和迁移均随包提供。镜像不复制 `.env`、私有配置、历史评测结果及开发虚拟环境。

Compose 包含 postgres、redis、elasticsearch、migrate、api、worker、scheduler；迁移成功后才启动业务进程，API 与 Worker 挂载同一 uploads 卷。基础设施对宿主机默认仅监听 127.0.0.1。生产环境通过外部环境变量/秘密管理配置凭据、存储备份与外部访问入口。

```bash
docker compose build
docker compose up -d
docker compose exec api rag-center doctor
docker compose logs --tail=100 api worker scheduler
```

Langfuse 使用可选 profile，SDK 使用可选依赖。启用时必须补齐 RAG_LANGFUSE_PUBLIC_KEY、RAG_LANGFUSE_SECRET_KEY、RAG_LANGFUSE_HOST，并构建包含 langfuse extra 的镜像。检索及 Research 请求只排队观测事件，容器关闭时在线程池统一 flush/shutdown 并关闭专用 HTTP 客户端；任一关闭失败仍继续释放其他连接池。

## 诊断与观测

`preflight` 只读检查配置、上传目录及已有父目录权限。`doctor` 进一步读取数据库、迁移 head、pgvector 扩展、实际向量列维度、Redis、启用的 Elasticsearch 和任务状态计数。输出仅包含状态和错误类型，失败时退出码为 1。

`/health/live` 检查进程存活；`/health/ready` 检查基础设施、迁移与维度，失败返回 503。JSON 日志关联 request_id/job_id，默认不记录正文及模型内容。Prometheus 指标仅使用路由模板、有限状态和任务类型，禁止把租户、文档、任务 ID 加为标签。Worker 指标为进程内计数，任务积压由数据库状态统计和就绪诊断提供；多进程汇总应由部署侧采集各实例。

## 任务恢复

1. Redis 中断时 outbox 保持 pending，调度器恢复后重发。发布与数据库标记之间崩溃允许重复消息。
2. Worker 中断时运行租约过期，调度器回收为 queued 并创建新 outbox；已到总尝试上限则任务及资源一起进入 failed。
3. 暂时性 Provider 失败按 available_at 退避。永久解析或参数失败直接终止。通过 `GET /api/v1/index-jobs/{job_id}` 查询任务，不手工把旧任务改成 succeeded。
4. 已发布但长期没有被领取的 queued 任务会按 redispatch 间隔补投；确认 Worker 的队列为 `indexing`，调度器和 Worker 使用同一 broker。
5. 新一代成功前旧产物保持可检索；失去租约的 Worker 无权发布。调度器清理过期且没有 PostgreSQL 发布记录的 Elasticsearch 代次，同时保留有效租约对应的准备中产物。
6. 任务达到失败终态后，修复依赖或文档内容，再用文档 index-jobs 接口或 CLI 创建新任务。

## 升级

升级前备份 PostgreSQL、上传卷和 Elasticsearch。停止旧 API、Worker 与旧调度入口，确认旧 Celery 消息格式；旧消息包含文档 ID，新消息只包含 job_id，应使用独立队列 `indexing`。迁移至 head 后启动新 API、Worker、调度器，运行 doctor 与冒烟检查，再放开流量。

0012 新增 index_jobs/outbox 及活动资源 partial unique index，0013 新增 available_at 和 queued partial index。调度器会为升级前没有任务记录的 processing 文档/building 版本补建任务与 outbox；升级窗口仍应核对资源数量及终态，确认历史处理没有遗漏。接口已进行破坏性规范化，前后端应一起升级。

回退时保持旧镜像和数据库备份。已发布的新增任务数据不能通过直接删除表恢复成旧队列协议；应停止消费并按备份恢复或前向修复。CI 冒烟使用专属 Compose 项目，清理 `down -v` 仅适用于其隔离测试数据。
