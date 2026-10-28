# Python 后端

负责游戏客户端适配、任务调度、SQLite 存储、OCR、知识检索与 stdio RPC。Agent 的工具注册、提示词和 skills 在 [`../agent/`](../agent/README.md)；网页对话界面在 [`../frontend/`](../frontend/README.md)。

## 目录

- `src/sgz_agent/`：Python 包及随包分发的 JavaScript 客户端桥接代码。
- `tests/`：离线回归测试。
- `config/`：配置模板、知识来源；`game.local.json` 包含本机设置，忽略提交。
- `scripts/`：依赖同步、客户端探针和显式运行的验证脚本。
- `pyproject.toml`、`uv.lock`：Python 项目元数据与锁定依赖。

## 环境与测试

需要 Python 3.11，运行环境统一使用仓库根目录 `.venv/`。从仓库根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File backend\scripts\Sync-Backend.ps1

Push-Location backend
..\.venv\Scripts\python.exe -m pytest -q
Pop-Location

# 查看 CLI 参数，不启动 worker
.venv\Scripts\python.exe -m sgz_agent --help
```

`Sync-Backend.ps1` 优先使用根目录 `.tools/uv/uv.exe`，否则使用 PATH 中的 uv；以冻结锁文件同步 dev、ocr、knowledge、api 依赖，并将 `UV_PROJECT_ENVIRONMENT` 临时设为根目录 `.venv`。

若仅需在已有环境中更新源码位置，可从仓库根目录执行：

```powershell
.tools\uv\uv.exe pip install --python .venv\Scripts\python.exe --no-deps --no-build-isolation --editable backend
```

## 配置与运行数据

CLI 默认读取本目录的 `config/game.local.json`，也可显式传入 `--config`。配置中的相对 `data_dir` 以配置文件目录为基准；省略该字段时默认使用 `../../.state`，与模板一致，指向仓库根目录的既有数据库、校准图和知识索引。自定义位置的配置文件应显式设置 `data_dir`。

Agent 启动与停止由 [`agent/scripts`](../agent/scripts/) 管理，正式 worker 由 DSH 插件启动。客户端验证脚本与 `pytest` 分开：`scripts/Test-Hunt*.py`、`scripts/Test-ReplenishAPI.py` 可能访问游戏，仅在对应操作获得授权时显式运行。

知识维护命令从仓库根目录执行，默认数据目录为 `.state/knowledge`：

```powershell
.venv\Scripts\python.exe -m sgz_agent.knowledge_cli status
.venv\Scripts\python.exe -m sgz_agent.knowledge_cli search --query "清除负面状态" --kind tactic --limit 5
```

维护前先退出 DSH 以释放知识 worker 的文件锁。能力与实机验收边界见 [Agent 说明](../agent/README.md) 和 [客户端契约](../docs/hunt-client-contract.md)。
