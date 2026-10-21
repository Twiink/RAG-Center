# 可观测性指南

## 概述

RAG-Center 提供多层次观测能力，用于生产环境的监控、诊断和性能分析：

- 结构化日志：JSON 格式，包含请求追踪、异常堆栈和敏感信息脱敏
- Prometheus 指标：HTTP、LLM、Research、Worker 等关键路径的延迟、吞吐和错误率
- 诊断命令：索引任务状态、租约统计、失败分析

## 日志系统

### 日志格式

生产环境默认输出 JSON 格式日志（`RAG_LOG_JSON=true`），每条包含：

```json
{
  "timestamp": "2026-10-21T10:30:45.123Z",
  "level": "INFO",
  "logger": "rag_center.modules.research",
  "message": "Research 运行完成",
  "request_id": "01a15e18-c17e-7ea3-a1f1-fa9efb94f2b8",
  "job_id": "job-12345",
  "service": "rag-center-worker",
  "exc_info": "Traceback...",
  "extra_field": "value"
}
```

### 敏感信息脱敏

日志系统自动脱敏以下内容：

- 字段名：`api_key`, `password`, `secret`, `token`, `authorization`
- Bearer token：`Authorization: Bearer xxx` → `Authorization: Bearer [REDACTED]`
- OpenAI 格式密钥：`sk-proj-xxx` → `sk-[REDACTED]`
- URL 凭据：`https://user:pass@host` → `https://[REDACTED]@host`
- URL 参数：`?token=xxx` → `?token=[REDACTED]`

### 日志配置

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `RAG_LOG_LEVEL` | `INFO` | 日志级别：DEBUG/INFO/WARNING/ERROR |
| `RAG_LOG_JSON` | `true` | 是否使用 JSON 格式 |
| `RAG_LOG_FILE_ENABLED` | `true` | 是否写入文件（容器内可设为 false） |
| `RAG_LOG_DIR` | `logs` | 日志目录 |
| `RAG_LOG_MODEL_CONTENT` | `false` | 是否记录 LLM 输入输出（调试用） |
| `RAG_SERVICE_NAME` | `rag-center` | 服务名标签（区分 api/worker/scheduler） |

## Prometheus 指标

### HTTP 指标

- `rag_http_requests_total{method, route, status}`: 请求计数
- `rag_http_request_duration_seconds{method, route}`: 请求延迟

### LLM 指标

- `rag_llm_requests_total{model, role, status}`: LLM 调用次数
- `rag_llm_request_duration_seconds{model}`: LLM 调用延迟
- `rag_llm_tokens_total{model, kind}`: Token 消耗（kind=prompt/completion）
- `rag_llm_cost_total{model}`: LLM 调用成本（美元）

### Research 指标

- `rag_research_runs_total{stop_reason, degraded}`: Research 运行次数
- `rag_research_stage_duration_seconds{stage}`: 各阶段耗时

### 索引任务指标

- `rag_index_job_attempts_total{type, status}`: 索引任务尝试次数
- `rag_index_jobs{status}`: 数据库任务数量
- `rag_index_lease_renew_failures_total`: 租约续期失败次数

### 检索指标

- `rag_retrieval_branch_failures_total{mode}`: 检索分支失败次数（mode=vector/bm25）
- `rag_degradations_total{component}`: 降级事件次数

### Worker 指标

- `rag_worker_heartbeat_timestamp{worker_id}`: Worker 心跳时间戳

### 暴露方式

API、Worker、Scheduler 各自在独立端口暴露 `/metrics` 端点：

- API: `/metrics` (HTTP 端口)
- Worker: `http://localhost:9090/metrics`（可配置 `RAG_METRICS_PORT`）
- Scheduler: `http://localhost:9090/metrics`

## 诊断命令

### 健康检查

```bash
# 预检查：数据库、向量库、Elasticsearch、Redis
uv run --directory Backend rag-center doctor

# 详细诊断
uv run --directory Backend rag-center check
```

### 索引任务诊断（规划中）

```bash
# 任务状态统计
rag-center jobs stats

# 最近失败任务
rag-center jobs failures --limit 20

# 过期租约
rag-center jobs expired-leases
```

## 告警规则（示例）

推荐的 Prometheus 告警规则：

```yaml
groups:
  - name: rag_center
    rules:
      - alert: HighErrorRate
        expr: rate(rag_http_requests_total{status=~"5.."}[5m]) > 0.05
        for: 5m
        annotations:
          summary: "HTTP 5xx 错误率超过 5%"

      - alert: LLMHighLatency
        expr: histogram_quantile(0.95, rag_llm_request_duration_seconds) > 30
        for: 5m
        annotations:
          summary: "LLM P95 延迟超过 30 秒"

      - alert: WorkerHeartbeatMissing
        expr: time() - rag_worker_heartbeat_timestamp > 120
        for: 2m
        annotations:
          summary: "Worker 心跳超过 2 分钟未更新"

      - alert: IndexJobFailureRate
        expr: rate(rag_index_job_attempts_total{status="failed"}[10m]) > 0.05
        for: 10m
        annotations:
          summary: "索引任务失败率超过 5%"
```

## 下一步

- Docker Compose observability profile（Prometheus + Grafana + 预设 Dashboard）
- OpenTelemetry 分布式追踪（可选 extra）
- Langfuse 集成（已有 `langfuse_enabled` 配置）
