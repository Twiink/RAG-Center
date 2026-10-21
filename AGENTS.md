# RAG-Center 开发约定

- 后端正式包为 `Backend/src/rag_center`，前端为 `Frontend`；参考 AgentTemplate 的配置、资源容器和统一 CLI 设计。
- Python 3.12.14、uv 0.12.22；依赖只修改 pyproject.toml 后更新 uv.lock。
- API → 业务服务 → 仓储/Provider。仓储只查询和 flush，业务服务管理事务；外部模型调用前释放无用事务。
- AsyncSession 不得跨并发任务共享；Research 分支传递 DTO 并各自创建会话。
- 后台索引必须经过 IndexJob/outbox，消息只传 job_id。所有产物发布验证有效租约，禁止旧 Worker 覆盖新代次。
- 后端 OpenAPI 为真源；接口变更后运行 `make openapi`。
- 注释和公共接口说明使用中文；项目文档和命令输出不使用 Emoji；秘密不写入代码、日志和记忆。
- 交付前运行 `make check`、`make frontend-check`；集成测试单独使用明确的 `_test` 数据库。
- 项目记忆 peer 固定为 `github.com-ai-moonlight-rag-center`，召回范围为 actor。必须核验手动 MCP 实际写入绑定；未确认时仅定向只读检索，工程结论写入本地待同步文件。

架构、开发、配置、接口和恢复流程见 `docs/`。
