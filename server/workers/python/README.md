# Python 执行器

这是 `server` 调用的子进程包（distribution：`sgz-dsh-agent`），通过 stdio JSON-RPC 接收工具请求，不监听 HTTP 端口。网站账号、会话和 DSH 调度由 [统一服务端](../../README.md) 管理；工具适配在 `server/src/agent/plugin/`，技能在 `server/skills/`。

按能力划分子包：

- `core/`：stdio RPC 与项目路径解析，无业务依赖。
- `analysis/`：战前评估纯计算，只需标准库；当前多用户服务启用此能力。
- `knowledge/`：资料索引、检索及独立 CLI；当前多用户服务未启用。
- `client/`：Windows、OCR、游戏接口与调度；保留历史实现，当前多用户服务未启用。

`client` 与 `knowledge` 不互相导入。旧的 `sgz_agent.cli`、`knowledge_cli`、`combat_assessment` 等 Python 导入入口保留兼容，业务实现只保留一份。

## 安装

需要 Python 3.11。以下命令从仓库根目录执行，环境保持在根 `.venv`：

```sh
# 服务端纯计算；离线测试加 --extra dev
UV_PROJECT_ENVIRONMENT="$PWD/.venv" uv sync --project server/workers/python --frozen --extra dev
# 需要单独验证知识库时增加对应依赖
UV_PROJECT_ENVIRONMENT="$PWD/.venv" uv sync --project server/workers/python --frozen --extra dev --extra knowledge
```

extras：`desktop`（mss/Pillow）、`ocr`（Paddle/Pillow/numpy）、`api`（Frida）、`knowledge`（qdrant-client/fastembed/portalocker）、`dev`（pytest）。`uv sync` 会按所选 extras 同步环境；切换依赖前先停止使用该环境的 worker。

Windows 可用 `scripts/Sync-Backend.ps1 -Profile chat -Dev`，`knowledge` 和 `local` profile 只控制依赖安装，不启动 DSH 或游戏。Python 解释器由服务端配置传入；单独执行时将下方 `.venv/bin/python` 换为 `.venv/Scripts/python.exe`。

## 入口与配置

```sh
.venv/bin/python -m sgz_agent.analysis.cli serve
.venv/bin/python -m sgz_agent.knowledge.cli status
.venv/bin/python -m sgz_agent.client.cli --help
```

计算 worker 不读取游戏配置、不创建游戏数据库或租约。服务端启动 worker 时传入仓库根目录作为工作目录及 `SGZ_PROJECT_ROOT`，因此源码和安装后的 wheel 都能定位同一项目。独立脚本优先使用 `SGZ_PROJECT_ROOT`，否则向上查找项目根目录；在项目外安装的 wheel 使用当前工作目录。

客户端显式 `--config` 优先；省略时先选现有 `server/workers/python/config/game.local.json`，否则使用 `server/workers/python/config/client/game.local.json`。需要配置客户端时，在所选目录复制 `game.example.json` 为 `game.local.json`，再完成设备身份校准。

相对 `data_dir` 以配置文件所在目录为基准。`config/` 缺省为 `../../../../.state`，`config/client/` 缺省为 `../../../../../.state`，均指向仓库根 `.state`。显式指定的外部旧路径仍保留原默认规则；自定义配置位置建议明确填写 `data_dir`。本次目录整理同时调整了已迁移本机配置的相对路径，保持它们指向原数据目录。

知识库默认仍为 `.state/knowledge`。来源 JSON 位于 `config/knowledge/`，内置机制种子随 Python 包分发；维护与 worker 共用文件锁，维护前释放对应 worker。

## 离线验证

```sh
# 基础包 + dev 即可；从仓库根目录执行
.venv/bin/python -m pytest server/workers/python/tests/test_config.py server/workers/python/tests/test_analysis_cli.py server/workers/python/tests/test_combat_assessment.py server/workers/python/tests/test_rpc.py server/workers/python/tests/test_dependency_contract.py -q
# 另需 knowledge 依赖
.venv/bin/python -m pytest server/workers/python/tests/test_module_boundaries.py server/workers/python/tests/test_knowledge.py server/workers/python/tests/test_knowledge_cli.py server/workers/python/tests/test_knowledge_sources.py server/workers/python/tests/test_knowledge_curated.py -q
```

完整客户端回归需要 Windows 的 `msvcrt` 文件锁及对应 extras；非 Windows 模拟测试不能替代实机验收。`scripts/Test-Hunt*.py`、`Test-ReplenishAPI.py` 等探测脚本可能访问游戏，应在独立授权的客户端验证中运行。DSH 与计算执行器的集成测试由根目录 `npm --prefix server test` 执行。
