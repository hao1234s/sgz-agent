# Python 后端

一个 `sgz-dsh-agent` distribution，按能力划分子包：

- `core/`：无业务依赖的 stdio RPC。
- `analysis/`：只计算输入的战前评估；标准库即可运行。
- `client/`：Windows、OCR、客户端接口、调度、存储、租约及 JS 资源。
- `knowledge/`：资料来源、索引、检索及独立 CLI；不加载客户端。

`client` 与 `knowledge` 不互相导入。原 `sgz_agent.cli`、`knowledge_cli`、`combat_assessment` 等入口保留兼容，业务实现只保留一份。工具注册与技能位于 [agent](../agent/README.md)。

## 按能力安装

需要 Python 3.11。基础安装不包含截图、OCR、Frida 或向量依赖。extras：`desktop`（mss/Pillow）、`ocr`（Paddle/Pillow/numpy）、`api`（Frida）、`knowledge`（qdrant-client/fastembed/portalocker）、`dev`（pytest）。

Windows 从仓库根目录执行：

```powershell
# chat 普通会话无需 Python；需要计算时安装基础包
powershell -ExecutionPolicy Bypass -File backend/scripts/Sync-Backend.ps1 -Profile chat -Dev
# 知识库，或原完整 Windows 环境
powershell -ExecutionPolicy Bypass -File backend/scripts/Sync-Backend.ps1 -Profile knowledge -Dev
powershell -ExecutionPolicy Bypass -File backend/scripts/Sync-Backend.ps1 -Profile local -Dev
```

Linux/macOS：

```sh
# 把环境放在仓库根 .venv，与插件默认解释器一致
UV_PROJECT_ENVIRONMENT="$PWD/.venv" uv sync --project backend --frozen --extra dev
# 按需增加知识库，不安装桌面/OCR/API
UV_PROJECT_ENVIRONMENT="$PWD/.venv" uv sync --project backend --frozen --extra dev --extra knowledge
```

`uv sync` 会按所选 extras 同步目标环境；如需同时保留本机完整能力，应使用 local 或显式列出原 extras。不要在运行中的 worker 环境上切换依赖。需要独立环境时使用不同的 `UV_PROJECT_ENVIRONMENT`，在插件对应模块的 `pythonPath` 中指定解释器。

## 入口和配置

以下示例从仓库根目录运行；Windows 将解释器换为 `.venv/Scripts/python.exe`：

```sh
.venv/bin/python -m sgz_agent.analysis.cli serve
.venv/bin/python -m sgz_agent.knowledge.cli status
.venv/bin/python -m sgz_agent.knowledge.cli search --query '清除负面状态' --kind tactic --limit 5
.venv/bin/python -m sgz_agent.client.cli --help
```

计算 worker 不读取游戏配置、不创建数据库或租约。知识库默认仍为 `.state/knowledge`，模型 `BAAI/bge-small-zh-v1.5` 和 collection `sgz_knowledge_v1` 不变；维护与 worker 共用文件锁，维护前释放对应知识 worker，不重建现有索引。

客户端显式 `--config` 优先；省略时先选现有 `backend/config/game.local.json`，否则使用 `backend/config/client/game.local.json`。新机器从后者同目录的 `game.example.json` 复制。本机配置与身份校准不自动搬迁或覆盖。

相对 `data_dir` 以配置文件为基准；旧 config 目录缺省为 `../../.state`，新 config/client 目录缺省为 `../../../.state`，均保持根 `.state`。自定义位置配置应显式填写 `data_dir`。旧知识来源 JSON 路径保留兼容副本，新路径为 `backend/config/knowledge/`；内置机制种子另随 Python 包分发，安装 wheel 后仍能读取。

## 离线验证

先安装 dev，以及所测能力的 extras。Windows 可使用：

```powershell
powershell -ExecutionPolicy Bypass -File agent/scripts/Test-Agent.ps1 -Profile chat
powershell -ExecutionPolicy Bypass -File agent/scripts/Test-Agent.ps1 -Profile knowledge
powershell -ExecutionPolicy Bypass -File agent/scripts/Test-Agent.ps1 -Profile local
```

macOS/Linux 在 backend 目录运行：

```sh
../.venv/bin/python -m pytest tests/test_analysis_cli.py tests/test_combat_assessment.py tests/test_rpc.py tests/test_dependency_contract.py -q
../.venv/bin/python -m pytest tests/test_knowledge.py tests/test_knowledge_cli.py tests/test_knowledge_sources.py tests/test_knowledge_curated.py tests/test_module_boundaries.py -q
```

完整客户端回归需要 Windows 的 `msvcrt` 文件锁和 local extras；非 Windows 的模拟锁测试不能替代原生验收。`scripts/Test-Hunt*.py`、`Test-ReplenishAPI.py` 与离线 pytest 分开，它们可能访问游戏，仅在用户授权后显式运行。插件打包、真实 DSH 的隔离 profile 验证见 [Agent 说明](../agent/README.md)。
