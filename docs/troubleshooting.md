# 故障排查指南

## 常见问题与解决方案

### Worker 无心跳

**症状**：
- Prometheus 指标 `rag_worker_heartbeat_timestamp` 长时间未更新
- 索引任务堆积在 `queued` 状态

**排查步骤**：
1. 检查 Worker 进程是否运行：`ps aux | grep celery`
2. 查看 Worker 日志：`tail -f logs/app.log | grep worker`
3. 检查 Redis 连接：`redis-cli -u $RAG_REDIS_URL ping`
4. 检查数据库连接：`uv run --directory Backend rag-center doctor`

**常见原因**：
- Worker 进程崩溃（OOM、未捕获异常）
- Redis 连接中断
- 数据库连接池耗尽

**恢复方案**：
```bash
# 重启 Worker
make worker

# 清理僵尸租约（需要手动或 Scheduler 自动回收）
# Scheduler 会在 index_job_redispatch_seconds 后回收过期租约
```

### 索引任务失败率高

**症状**：
- `rag_index_job_attempts_total{status="failed"}` 比例超过 5%
- 文档长时间处于 `processing` 状态

**排查步骤**：
1. 查看失败任务错误：
   ```sql
   SELECT job_type, error_message, COUNT(*)
   FROM index_jobs
   WHERE status='failed' AND updated_at > NOW() - INTERVAL '1 hour'
   GROUP BY job_type, error_message;
   ```

2. 按错误类型分类：
   - LLM 限流：`error_message LIKE '%rate limit%'`
   - LLM 超时：`error_message LIKE '%timeout%'`
   - Elasticsearch 错误：`error_message LIKE '%elasticsearch%'`
   - 解析错误：`error_message LIKE '%parse%'`

**常见原因**：
- LLM API 限流或超时（需要降低并发或增加配额）
- Elasticsearch 集群负载过高
- PDF/DOCX 解析库兼容性问题

**恢复方案**：
```bash
# 降低 Worker 并发
celery -A rag_center.celery_app worker --concurrency=2

# 重试失败任务（手动 SQL）
UPDATE index_jobs SET status='queued', attempt=0 WHERE status='failed';
```

### LLM 调用失败

**症状**：
- `rag_llm_requests_total{status="error"}` 持续增长
- API 返回 502/504 错误

**排查步骤**：
1. 查看 LLM 日志：`tail -f logs/app.log | grep LLM_EXCEPTION`
2. 检查错误类型：
   - `LLM_TIMEOUT`: 超时（检查网络或增加 `RAG_LLM_TIMEOUT_SECONDS`）
   - `LLM_RATE_LIMIT`: 限流（降低 QPS 或增加配额）
   - `LLM_TOKEN_LIMIT`: Token 超限（减少输入或使用更大上下文模型）
   - `LLM_PROVIDER_FAILED`: 提供商故障（5xx 或连接错误）

**常见原因**：
- API 密钥无效或过期
- 余额不足
- 区域限制或网络问题
- 模型名称错误

**恢复方案**：
```bash
# 检查配置
uv run --directory Backend rag-center check

# 测试 LLM 连接（使用 model_stub 模拟）
RAG_LLM_BASE_URL=http://localhost:9000 make test
```

### Research 超时或降级

**症状**：
- `rag_research_runs_total{stop_reason="timeout"}` 增长
- `rag_degradations_total{component="research"}` 增长

**排查步骤**：
1. 查看各阶段耗时：
   ```promql
   histogram_quantile(0.95, rag_research_stage_duration_seconds_bucket)
   ```

2. 识别瓶颈：
   - `planning`: Planner LLM 慢
   - `retrieving`: 向量库或关键词检索慢
   - `deciding`: Decider LLM 慢
   - `finalizing`: Finalizer LLM 慢

**常见原因**：
- LLM 模型选择过慢（使用 `llm_strong_model`）
- 向量库查询慢（索引未优化或数据量大）
- 并发检索任务过多

**恢复方案**：
```bash
# 使用更快的模型
RAG_LLM_MODEL=deepseek-v4-flash

# 禁用 Research（降级为单轮检索）
# 需要在 API 调用中传递 research_enabled=false
```

### Elasticsearch 连接失败

**症状**：
- `rag_retrieval_branch_failures_total{mode="bm25"}` 增长
- API 返回 502 错误，日志显示 Elasticsearch 超时

**排查步骤**：
1. 检查 Elasticsearch 健康：
   ```bash
   curl $RAG_ELASTICSEARCH_URL/_cluster/health
   ```

2. 检查索引状态：
   ```bash
   curl $RAG_ELASTICSEARCH_URL/$RAG_ELASTICSEARCH_INDEX/_stats
   ```

**常见原因**：
- Elasticsearch 集群负载过高
- 分片未分配（red 状态）
- 网络问题

**恢复方案**：
```bash
# 临时禁用 Elasticsearch，使用纯向量检索
RAG_ELASTICSEARCH_ENABLED=false

# 或切换检索模式
RAG_RETRIEVAL_MODE=vector
```

### 租约续期失败

**症状**：
- `rag_index_lease_renew_failures_total` 增长
- 任务在接近完成时被其他 Worker 重复执行

**排查步骤**：
1. 查看续期失败日志：`grep "lease_renew_failed" logs/app.log`
2. 检查数据库连接池：`SHOW max_connections;`

**常见原因**：
- 数据库连接池耗尽
- 任务执行时间超过租约时长（`RAG_INDEX_JOB_LEASE_SECONDS`）
- 网络抖动导致续期请求失败

**恢复方案**：
```bash
# 增加租约时长
RAG_INDEX_JOB_LEASE_SECONDS=600

# 增加数据库连接池
RAG_DATABASE_POOL_SIZE=20
RAG_DATABASE_MAX_OVERFLOW=40
```

## 日志关键字速查

| 关键字 | 含义 | 日志级别 |
|--------|------|---------|
| `LLM_EXCEPTION` | LLM 调用失败 | ERROR |
| `RETRIEVAL_FAILED` | 检索失败 | ERROR |
| `lease_renew_failed` | 租约续期失败 | WARNING |
| `degraded=true` | 降级运行 | INFO |
| `stop_reason=timeout` | Research 超时 | INFO |
| `error_code=LLM_RATE_LIMIT` | LLM 限流 | WARNING |

## 性能分析

### 识别慢查询

```sql
-- 最近 1 小时索引任务耗时分布
SELECT
    job_type,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (updated_at - created_at))) AS p50_seconds,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (updated_at - created_at))) AS p95_seconds
FROM index_jobs
WHERE status='completed' AND created_at > NOW() - INTERVAL '1 hour'
GROUP BY job_type;
```

### LLM 成本分析

```promql
# 最近 24 小时各模型成本
increase(rag_llm_cost_total[24h])

# 成本趋势
rate(rag_llm_cost_total[1h])
```

### 检索延迟热点

```promql
# 各模式 P95 延迟
histogram_quantile(0.95, rate(rag_http_request_duration_seconds_bucket{route="/retrieve"}[5m]))

# Research 阶段耗时分布
histogram_quantile(0.95, rate(rag_research_stage_duration_seconds_bucket[5m])) by (stage)
```

## 紧急降级开关

生产环境遇到严重故障时，可以快速降级功能：

```bash
# 禁用 Research，只用单轮检索
# 需要在应用代码中检查 research_enabled 配置

# 禁用 Rerank
RAG_RERANK_ENABLED=false

# 禁用 Evidence 编排
RAG_EVIDENCE_ENABLED=false

# 禁用 Query 改写
RAG_QUERY_REWRITE_ENABLED=false

# 切换为纯向量检索（最快）
RAG_RETRIEVAL_MODE=vector

# 禁用 Elasticsearch
RAG_ELASTICSEARCH_ENABLED=false
```

## 联系支持

如果以上步骤无法解决问题，请收集以下信息：

1. 错误日志：`logs/app.log` 和 `logs/error.log` 的相关时间段
2. 系统状态：`uv run --directory Backend rag-center doctor` 输出
3. 指标快照：Prometheus `/metrics` 端点或 Grafana 截图
4. 复现步骤：最小化的复现请求示例
