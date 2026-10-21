# 配置与来源

配置优先级从低到高为：类型默认值、包内 `config/base.yaml`、工作目录 `config/environments/<环境>.yaml`、`config/private.yaml`、`.env`、进程环境变量、显式覆盖。

源码仓库从根目录调用时识别 `Backend` 为配置目录；推荐通过 Makefile 或 `uv run --directory Backend rag-center ...` 调用。wheel 在当前工作目录查找外部配置。`RAG_CONFIG_DIR` 可以覆盖外部 YAML 目录。`RAG_APP_ENV` 支持 local、test、production。

```yaml
# Backend/config/private.yaml；禁止提交该文件
model:
  base_url: http://localhost:9000/v1
embedding:
  model: text-embedding-3-small
  dimensions: 1536
index_job:
  lease_seconds: 300
  max_attempts: 3
```

环境变量使用 `RAG_` 前缀，支持字段形式 `RAG_DATABASE_URL` 和分组形式 `RAG_DATABASE__URL`。同一个配置只选一种形式。密钥字段使用 SecretStr；密码型 URL 展示时移除凭据及查询参数。

| 配置 | 用途 |
| --- | --- |
| `RAG_DATABASE_URL` | `postgresql+psycopg://` 数据库连接 |
| `RAG_REDIS_URL` | 异步限流与配额存储 |
| `RAG_CELERY_BROKER_URL` / `RAG_CELERY_RESULT_BACKEND` | 队列与任务结果 |
| `RAG_MODEL_BASE_URL` / `RAG_MODEL_API_KEY` | Embedding 服务 |
| `RAG_LLM_BASE_URL` / `RAG_LLM_API_KEY` | 改写与 Research 模型 |
| `RAG_ELASTICSEARCH_ENABLED` / `RAG_ELASTICSEARCH_URL` | 关键词搜索依赖 |
| `RAG_DOCUMENT_STORAGE_PATH` | API/Worker 共享上传目录 |
| `RAG_INDEX_JOB_LEASE_SECONDS` | 租约时长，按三分之一间隔续租 |
| `RAG_INDEX_JOB_MAX_ATTEMPTS` | 默认三次总尝试 |
| `RAG_INDEX_JOB_RETRY_BACKOFF_SECONDS` | 指数退避起始秒数 |
| `RAG_INDEX_JOB_REDISPATCH_SECONDS` | 已投递但长期 queued 的补投间隔 |
| `RAG_INDEX_OUTBOX_INTERVAL_SECONDS` | 调度轮询间隔 |
| `RAG_PARSER_MAX_CONCURRENCY` | 阻塞解析/分块线程池大小 |
| `RAG_LOG_JSON` | 默认输出结构化 JSON 日志 |
| `RAG_LOG_FILE_ENABLED` | 是否写入文件，容器内可关闭（默认 true） |
| `RAG_LOG_DIR` | 日志目录（默认 logs） |
| `RAG_SERVICE_NAME` | 服务名标签，区分 api/worker/scheduler |
| `RAG_LOG_REQUEST_BODY` / `RAG_LOG_RESPONSE_BODY` / `RAG_LOG_MODEL_CONTENT` | 默认关闭正文及模型内容日志 |
| `RAG_METRICS_ENABLED` / `RAG_METRICS_PORT` | Prometheus 指标开关和端口（默认 true, 9090） |
| `RAG_LLM_MAX_RETRIES` / `RAG_LLM_RETRY_DELAY_SECONDS` | LLM 重试配置（默认 2, 1.0） |
| `RAG_LANGFUSE_ENABLED` / `RAG_LANGFUSE_*` | 可选观测；启用时要求配置公私钥 |

运行 `rag-center config show` 查看脱敏值及各字段来源，运行 `rag-center config validate` 校验。分块 overlap 必须小于 chunk_size，HTTP keepalive 不能超过总连接数。向量维度固定为迁移定义的 1536，修改维度必须先设计新的数据迁移及重建流程。

测试使用显式 `Settings(...)` 和 `create_app(settings)` 注入，配置通过请求/任务上下文传播，避免多个测试应用互相覆盖。`.env` 和私有 YAML 不能进入 Git、镜像或远程记忆。
