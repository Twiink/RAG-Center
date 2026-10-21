# RAG-Center 工程化完善 - 第一阶段交付报告

**日期**: 2026-10-21
**版本**: Phase 1 - 观测基础设施
**状态**: 已完成并通过验证

## 实施总结

完成了观测基础设施的核心能力交付，为生产部署提供日志、指标和故障诊断的基础。

## 核心交付物

### 1. 日志增强

**文件**: `Backend/src/rag_center/core/logging.py`

**新增功能**:
- `redact_sensitive(text)`: 脱敏 Bearer token、sk- 密钥、URL 凭据
- `redact_dict(data)`: 递归脱敏字典中的敏感字段（api_key/password/secret/token/authorization）
- `JsonFormatter`: 输出 exc_info（异常堆栈）和 extra 字段（结构化上下文）
- 服务名标签：通过 `RAG_SERVICE_NAME` 区分 api/worker/scheduler

**配置项**:
- `RAG_LOG_FILE_ENABLED`: 是否写入文件（默认 true，容器内可设为 false）
- `RAG_LOG_DIR`: 日志目录（默认 logs）
- `RAG_SERVICE_NAME`: 服务名标签

**测试覆盖**: `tests/test_logging_redaction.py` (9 passed)

### 2. Prometheus 指标扩展

**文件**: `Backend/src/rag_center/core/metrics.py`

**新增指标**:

| 指标组 | 指标名 | 标签 | 说明 |
|--------|--------|------|------|
| LLM | `rag_llm_requests_total` | model, role, status | 调用次数 |
| LLM | `rag_llm_request_duration_seconds` | model | 调用延迟 |
| LLM | `rag_llm_tokens_total` | model, kind | Token 消耗（kind=prompt/completion） |
| LLM | `rag_llm_cost_total` | model | 成本（美元） |
| Embedding | `rag_embedding_requests_total` | model, status | 调用次数 |
| Embedding | `rag_embedding_duration_seconds` | model | 延迟 |
| Rerank | `rag_rerank_requests_total` | model, status | 调用次数 |
| Rerank | `rag_rerank_duration_seconds` | model | 延迟 |
| Research | `rag_research_runs_total` | stop_reason, degraded | 运行次数 |
| Research | `rag_research_stage_duration_seconds` | stage | 阶段耗时 |
| 检索 | `rag_retrieval_branch_failures_total` | mode | 分支失败次数 |
| 降级 | `rag_degradations_total` | component | 降级事件次数 |
| 限流 | `rag_ratelimit_rejections_total` | kind | 拒绝次数 |
| Worker | `rag_index_lease_renew_failures_total` | - | 租约续期失败次数 |
| Worker | `rag_worker_heartbeat_timestamp` | worker_id | 心跳时间戳 |

**设计原则**:
- 标签严格限制低基数（model/role/status/stage），不使用 tenant_id/document_id
- 每个进程使用独立 registry，避免 multiprocess 冲突

**配置项**:
- `RAG_METRICS_ENABLED`: 是否启用（默认 true）
- `RAG_METRICS_PORT`: Worker/Scheduler 暴露端口（默认 9090）

### 3. LLM 韧性工具

**文件**: `Backend/src/rag_center/infrastructure/providers/llm/resilience.py`

**新增功能**:
- `classify_llm_error(exc)`: 错误分类，返回 (ErrorCode, retryable)
  - LLM_TIMEOUT: 超时（可重试）
  - LLM_RATE_LIMIT: 限流（可重试）
  - LLM_TOKEN_LIMIT: Token 超限（不可重试）
  - LLM_NO_RESPONSE: 无响应（不可重试）
  - LLM_PROVIDER_FAILED: 提供商故障（可重试）
- `retry_with_backoff(func, max_retries, initial_delay, model)`: 指数退避重试
- `record_llm_metrics(...)`: 指标记录辅助函数

**配置项**:
- `RAG_LLM_MAX_RETRIES`: 最大重试次数（默认 2）
- `RAG_LLM_RETRY_DELAY_SECONDS`: 初始延迟（默认 1.0）

**测试覆盖**: `tests/test_llm_resilience.py` (9 passed)

### 4. 异常增强

**文件**: `Backend/src/rag_center/core/exceptions.py`

**改动**: 增加 LLM 错误码到 HTTP 状态码的映射
- LLM_TIMEOUT → 504
- LLM_RATE_LIMIT → 429
- LLM_TOKEN_LIMIT → 422
- LLM_NO_RESPONSE → 502
- LLM_PROVIDER_FAILED → 502

### 5. 文档

**新增**:
- `docs/observability.md`: 日志、指标、诊断命令、告警规则指南
- `docs/troubleshooting.md`: 常见故障排查（Worker 无心跳、任务失败、LLM 错误、Research 超时、ES 连接、租约失败等）

**更新**:
- `docs/configuration.md`: 新增配置项说明
- `docs/engineering-memory-pending.md`: 记录本次完善的工程结论

## 测试验证

### 单元测试
- **总计**: 254 passed (基线 236 + 新增 18)
- **新增**:
  - `tests/test_logging_redaction.py`: 9 passed
  - `tests/test_llm_resilience.py`: 9 passed
- **工程契约**: 8 passed

### 质量检查
- ✅ Ruff 格式检查
- ✅ Ruff lint 检查
- ✅ Type checking (pyright)
- ✅ 所有单元测试通过

## 与原计划的偏差

### 调整项

1. **OpenTelemetry**: 原计划称"无需额外 SDK"，实际需要新增依赖。**调整**：暂不引入 OTel，只用 Prometheus + 结构化日志。

2. **测试基线**: 原计划称"256+ 单元测试"，实际为 236 passed。**调整**：按实际基线验收（254 passed）。

3. **降级策略**: Rerank、planner/decider/finalizer 降级已存在。**调整**：Research 超时自动降级推迟到下阶段。

### 推迟项（下一阶段）

以下功能因范围或复杂度原因推迟：

1. **LLM Provider 集成重试和指标**: 需要修改 `openai_compatible.py` 的多个调用点，已提供 `resilience.py` 工具模块但未集成。

2. **Research 超时自动降级**: 需要用 `asyncio.timeout` 包装各阶段，当前只在阶段间检查。

3. **CLI 诊断命令**: `jobs stats`/`jobs failures`/`jobs expired-leases` 框架已在 `cli/commands.py`，具体实现推迟。

4. **安全检查**: `production + auth_enabled=false` 应报错，需要在 `Settings.validate_relationships` 中增加。

5. **Observability Compose**: Prometheus + Grafana + Dashboard 的 Docker Compose profile。

6. **Worker/Scheduler 指标暴露**: 需要在各自进程启动时调用 `prometheus_client.start_http_server(settings.metrics_port)`。

7. **范围过大项**:
   - Webhook 推送 cost（需要新接口和 schema 变更）
   - 前端 degraded 提示（需要 `make openapi` + `make frontend-check`）
   - 故障注入 `__slow__` 标志（安全风险，只放 model_stub）
   - A/B feature flags（需要数据库迁移）

## 下一步建议

### 第二优先级（近期）
1. 集成 LLM Provider 重试和指标到 `openai_compatible.py`
2. Research 各阶段增加超时上下文管理器
3. Worker/Scheduler 启动时暴露 `/metrics` 端点
4. CLI 诊断命令实现

### 第三优先级（中期）
1. Docker Compose observability profile（Prometheus + Grafana）
2. 预设 Grafana Dashboard（API/Worker/Scheduler 时序，Research 分布，成本趋势）
3. 安全检查（production + auth_enabled=false）
4. 限流服务原子化（Redis pipeline 或 Lua）

### 第四优先级（可选）
1. OpenTelemetry 分布式追踪（作为可选 extra）
2. Langfuse 集成增强（成本推送、trace 关联）
3. 前端 degraded 标志展示

## 交付验收

- [x] 254 单元测试通过（基线 236 + 新增 18）
- [x] 工程契约测试 8 passed
- [x] Ruff 格式和 lint 检查通过
- [x] Type checking 通过
- [x] 日志脱敏功能覆盖敏感信息类型
- [x] Prometheus 指标扩展涵盖 LLM/Embedding/Rerank/Research/Worker
- [x] LLM 错误分类和重试工具单元测试通过
- [x] 文档覆盖观测和故障排查
- [ ] Observability Compose 启动后 Grafana 可视化（推迟）
- [ ] CLI `analyze-traces` 复现故障场景（推迟）
- [ ] Production Dockerfile HEALTHCHECK 和 /metrics 端点（推迟）

## 工程质量改进

1. **测试覆盖率提升**: 新增 18 个单元测试，覆盖日志脱敏和 LLM 韧性关键路径
2. **可观测性基础**: 为生产部署提供日志、指标和诊断的基础能力
3. **错误处理规范**: LLM 错误分类和 HTTP 状态码映射，便于前端和监控识别
4. **文档完善**: 新增观测指南和故障排查文档，降低运维学习成本

## 依据

- 当前工作区代码及测试套件验证
- 参考 `AgentTemplate` 观测实践
- OpenViking 规范（peer: `github.com-ai-moonlight-rag-center`，actor 召回）

---

**注**: 本阶段聚焦观测基础设施，为后续集成和生产部署奠定基础。推迟项不影响当前交付质量，可按优先级逐步实施。
