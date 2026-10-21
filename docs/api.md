# API 契约

业务接口前缀为 `/api/v1`。身份只来自验证后的 Bearer API Key，客户端传入的租户字段不能覆盖凭据身份。交互文档在 `/docs`，OpenAPI 在 `/openapi.json`。

成功与业务错误统一为 `code/msg/data/request_id`；响应头 `X-Request-ID` 与正文一致。删除成功返回 204 空正文。认证失败为 401、无功能权限为 403、参数校验为 422、资源不存在为 404、活动任务冲突为 409、限流为 429，外部依赖失败返回相应 5xx。

| 方法和路径 | 成功状态 | 行为 |
| --- | --- | --- |
| `POST /knowledge-bases` | 201 | 创建知识库 |
| `POST /knowledge-bases/{kb_id}/documents` | 202 | JSON title/content 或 multipart file/title；返回 job_id |
| `POST /documents/{document_id}/index-jobs` | 202 | 可选 reparse；返回 job_id |
| `POST /knowledge-bases/{kb_id}/index-versions` | 202 | 新版本构建，返回版本及 job_id |
| `PUT /knowledge-bases/{kb_id}/active-index-version` | 200 | 请求 `{"version":"v2"}` |
| `GET /index-jobs/{job_id}` | 200 | queued/running/succeeded/failed、尝试数及错误 |
| `DELETE /documents/{document_id}` | 204 | 删除文档 |
| `DELETE /knowledge-bases/{kb_id}` | 204 | 删除知识库 |
| `DELETE /knowledge-bases/{kb_id}/index-versions/{version}` | 204 | 删除未激活且未构建中的版本 |
| `POST /rag/retrieve` | 200 | 保留检索业务含义 |
| `POST /rag/research` | 200 | 保留受控 Research 契约与套餐限制 |
| `POST /rag/feedback` | 200 | 校验 trace 所属租户后提交反馈 |

上传返回 202 表示任务和 outbox 已持久化。客户端通过 job_id 轮询终态；任务失败时读取 error_message。重复消息由服务端 claim 吸收，同一资源已有活动任务时拒绝新建任务。

后端为接口真源。成功响应使用泛型 `ApiResponse[T]`，常见错误状态及 422 校验失败统一声明为 `ApiErrorResponse`。运行 `make openapi` 更新 `Frontend/openapi.json` 和 `Frontend/src/types/openapi.d.ts`；前端服务使用生成的核心数据类型，业务视图类型可在其基础上派生。CI 检查导出契约与生成类型漂移。

健康检查为 `/health/live` 与 `/health/ready`，Prometheus 文本指标为 `/metrics`。这些运维路径不使用业务响应封装。
