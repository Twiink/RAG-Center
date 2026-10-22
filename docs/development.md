# 开发与测试

## 命令

| 命令 | 行为 |
| --- | --- |
| `rag-center init` | 初始化上传目录 |
| `rag-center config show` / `validate` | 脱敏查看来源、交叉校验 |
| `rag-center dev` / `serve` | 开发热更新、生产启动 |
| `rag-center worker run` | prefork Worker；支持 concurrency 和 queue |
| `rag-center scheduler run` | outbox、补投、租约恢复及失败产物清理 |
| `rag-center migrate` | 从 wheel 内加载 Alembic 迁移 |
| `rag-center tenant create` / `list` | 租户管理 |
| `rag-center key create` / `list` / `revoke` | 凭据管理；创建写入 0600 文件 |
| `rag-center index rebuild` | 构建知识库新版本 |
| `rag-center index reindex-document` | 重新索引文档 |
| `rag-center eval` | 数据集/实验校验、运行、评分、报告及精排重试 |
| `rag-center openapi` | 无外部连接导出接口契约 |
| `rag-center check` | 格式、lint、完整正式包 ty、单元测试 |
| `rag-center preflight` / `doctor` | 只读本地预检、外部依赖诊断 |

Makefile 包装这些入口，IDE 使用 `Backend/.venv/bin/python` 与 `rag_center.main`。旧 `scripts/*.py` 运维入口已并入 CLI，评测代码在正式包内，不再修改 sys.path。数据集、实验及历史报告位于 `Backend/eval`，不装入 wheel。

## Git hooks

首次克隆后执行一次：

```bash
sh scripts/install-git-hooks.sh
```

脚本设置 `core.hooksPath=.githooks`，启用以下检查：

| Hook | 行为 |
| --- | --- |
| `pre-commit` | 对暂存的 `Backend` Python 文件运行 Ruff；拒绝同一文件部分暂存 |
| `commit-msg` | 校验 `<type>(<scope>): <subject>` 格式，主题必须包含中文且仅允许一行 |
| `pre-push` | 校验 `Backend/uv.lock` 与项目一致，并对 `Backend` 运行 Ruff |
| `post-merge` | hook 变更后自动刷新；后端或前端依赖变更时提示同步 |
| `pre-rebase` | 禁止直接 rebase `main`、`master`、`release/*` |

提交示例：`feat(backend): 新增索引任务诊断命令`。确需在受保护分支 rebase 时，临时设置 `RAG_CENTER_ALLOW_PROTECTED_REBASE=1`。

## 测试

单元测试默认不运行 integration 标记。新增测试应覆盖契约与失效条件，外部模型通过本地 HTTP 协议桩或 MockTransport 替代。套餐 quality 能力由测试显式设置，覆盖部署开关启用和关闭两种行为。

集成测试要求 `RAG_TEST_DATABASE_URL`，数据库名必须以 `_test` 结尾，先执行 `RAG_DATABASE_URL=<测试库> rag-center migrate`。测试创建独立租户并清理自身数据，不删除开发数据库。Redis、Elasticsearch 同样应使用隔离实例。

`tests/support/model_stub.py` 提供本地 embeddings、rerank 和聊天协议；`tests/support/compose_smoke.py` 通过真实 API/Worker/调度器连续执行任务并检索。CI 的 `Deploy/compose.ci.yaml` 使用独立数据库和测试模型服务。CI 先启动基础设施并执行迁移、集成测试，再启动业务消费者执行 Compose 冒烟，避免调度器领取集成测试自行控制的任务。

集成测试覆盖文本/文件上传、检索模式、版本构建/激活、并发切换、租户隔离、重复投递、broker 中断、过期续租拒绝、旧代次发布拒绝、失败产物清理、重试耗尽、历史任务恢复，以及真实 prefork 进程中断后恢复。测试可以强制结束自己创建的 Worker 进程组，不操作开发环境 Worker。

## 打包与检查

```bash
uv sync --directory Backend --locked
make check
make frontend-check
make openapi
uv build --project Backend --wheel
```

可选依赖用 `uv sync --directory Backend --extra eval` 或 `--extra langfuse` 安装。正式包的配置与迁移在源码外仍须可访问，CI 在 `/tmp` 创建隔离虚拟环境验证 wheel。
