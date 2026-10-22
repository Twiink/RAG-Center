# 依赖版本锁定策略

## 概述

RAG-Center 采用 **双层锁定**：
- **pyproject.toml**：使用 `~=X.Y.Z` 锁定小版本范围，防止破坏性升级
- **uv.lock**：锁定完整依赖图的精确版本，保证可复现构建

## 版本约束语义

- `~=X.Y.Z`：兼容发布约束，等价于 `>=X.Y.Z, <X.(Y+1).0`
- `~=X.Y`：等价于 `>=X.Y.0, <X.(Y+1).0`（0.x 包适用）
- `~=0.0.Z`：对于 0.0.x 包，等价于 `>=0.0.Z, <0.1.0`

## 锁定分类

### 1. 核心框架（严格锁定）

```toml
alembic~=1.19.0          # 数据库迁移：DDL 语义变化风险
fastapi~=0.141.0         # Web 框架：路由和依赖注入行为
pydantic~=2.13.0         # 数据验证：序列化/验证规则
sqlalchemy~=2.0.0        # ORM：查询 API 和事务语义
celery~=5.6.0            # 任务队列：协议兼容性
```

**理由**：这些组件的小版本升级可能改变行为或 API，锁定到小版本范围可平衡稳定性与安全修复。

### 2. OpenTelemetry 仪表套件（协调升级）

```toml
opentelemetry-sdk~=1.45.0
opentelemetry-exporter-otlp-proto-http~=1.45.0
opentelemetry-instrumentation-fastapi~=0.66b0
opentelemetry-instrumentation-httpx~=0.66b0
opentelemetry-instrumentation-sqlalchemy~=0.66b0
opentelemetry-instrumentation-redis~=0.66b0
opentelemetry-instrumentation-celery~=0.66b0
```

**理由**：
- SDK 和 exporter 必须匹配协议版本
- 仪表包之间共享内部 API，必须同步升级
- `0.66b0` 使用 `~=` 锁定在 `>=0.66b0, <0.67` 范围

### 3. 数据库与存储（小版本锁定）

```toml
psycopg~=3.3.0           # PostgreSQL 驱动：连接协议
pgvector~=0.5.0          # 向量扩展：查询 API
redis~=6.4.0             # Redis 客户端：命令映射
```

### 4. HTTP 与网络（小版本锁定）

```toml
httpx~=0.28.0            # HTTP 客户端：连接池行为
uvicorn~=0.52.0          # ASGI 服务器：lifespan 处理
```

### 5. 文档处理（允许小版本升级）

```toml
mammoth~=1.12.0          # DOCX 转换
pymupdf~=1.28.0          # PDF 解析
markdown-it-py~=4.2.0    # Markdown 解析
markdownify~=1.2.0       # HTML 转 Markdown
mdit-py-plugins~=0.6.0   # Markdown 扩展
```

### 6. 开发工具（跟随最新稳定）

```toml
pytest~=9.1.0            # 测试框架：fixture API
pytest-asyncio~=1.4.0    # 异步测试：事件循环管理
ruff~=0.16.0             # 代码检查：规则和格式
ty>=0.0.1a5              # 类型检查：alpha 版本保持宽松
```

### 7. 评估与跟踪（可选依赖）

```toml
[project.optional-dependencies]
langfuse = ["langfuse~=2.60.0"]
eval = [
  "datasets~=5.1.0",
  "openai~=3.35.0",
  "ragas~=0.3.0",
]
```

## 运行时锁定

### Python 版本

```toml
requires-python = ">=3.12,<3.13"
```

锁定到 Python 3.12.x，避免 3.13 潜在的不兼容变更。

### uv 版本

```toml
[tool.uv]
required-version = "==0.12.22"

[build-system]
requires = ["uv_build==0.12.22"]
```

构建和依赖管理工具使用精确版本，保证锁文件解析一致性。

## 升级流程

### 1. 小版本安全更新（每月）

```bash
# 在约束范围内升级
cd Backend
uv lock --upgrade

# 验证
uv sync --locked
make check
uv run pytest tests/ -m "not integration"
```

### 2. 跨小版本升级（季度）

1. 检查 CHANGELOG 确认破坏性变更
2. 更新 pyproject.toml 约束
3. 运行完整测试套件（含集成测试）
4. 更新本文档记录升级依据

### 3. 主版本升级（按需）

需要专门的迁移计划：
- 评估 API 变更影响范围
- 创建兼容层或重构受影响代码
- 分阶段部署验证

## 故障降级

如遇依赖导致的生产问题：

```bash
# 回退到已知良好版本
git checkout <last-good-commit> -- Backend/uv.lock
cd Backend && uv sync --locked

# 或临时固定问题包
uv add "问题包名==已知良好版本"
```

## CI/CD 集成

### Dockerfile

```dockerfile
# 使用固定 uv 版本
COPY --from=ghcr.io/astral-sh/uv:0.12.22 /uv /usr/local/bin/uv

# 优先使用锁文件
RUN uv sync --locked --no-dev
```

### GitHub Actions

```yaml
- uses: astral-sh/setup-uv@v4
  with:
    version: "0.12.22"

- run: uv sync --locked
- run: uv run pytest tests/
```

`--locked` 标志确保 CI 使用与开发环境完全相同的依赖版本。

## 依赖审计

定期检查已知漏洞：

```bash
# 使用 uv 内置审计（未来版本）
uv audit

# 或使用 pip-audit
uvx pip-audit
```

## 锁文件冲突解决

多人协作时 uv.lock 冲突：

```bash
# 拉取最新代码后重新解析
git pull
cd Backend
uv lock --upgrade

# 如需保持他人的锁定版本
uv sync --locked
```

**不要手工编辑 uv.lock**，始终通过 `uv lock` 重新生成。

## 版本记录

| 组件                  | 当前版本   | 锁定时间   | 下次审查   |
|-----------------------|-----------|-----------|-----------|
| Python                | 3.12.14   | 2026-10   | 2027-04   |
| uv                    | 0.12.22   | 2026-10   | 2027-01   |
| FastAPI               | 0.141.0   | 2026-10   | 2027-01   |
| SQLAlchemy            | 2.0.0     | 2026-10   | 2027-01   |
| OpenTelemetry SDK     | 1.45.0    | 2026-10   | 2027-01   |
| Celery                | 5.6.0     | 2026-10   | 2027-01   |

## 参考资料

- [PEP 440 - Version Specifiers](https://peps.python.org/pep-0440/)
- [uv Documentation - Lockfile](https://docs.astral.sh/uv/concepts/lockfile/)
- [Semantic Versioning 2.0.0](https://semver.org/)
