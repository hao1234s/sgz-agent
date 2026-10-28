# 三国志 Agent 工作台

项目按职责分为 Agent、后端、前端三个目录。

```text
agent/
  plugin/                 DSH 工具注册、提示词、Python worker 管理
  skills/                 Agent 技能与操作流程
  scripts/                Agent 启动、插件安装和集成验证
  README.md               Agent 功能与使用说明
backend/
  src/sgz_agent/          Python 服务、任务调度、存储、客户端适配
  tests/                  后端回归测试
  config/                 配置模板、知识来源、本机配置
  scripts/                后端环境初始化、客户端验证与探针
  pyproject.toml          Python 依赖与包配置
  uv.lock                 Python 依赖锁
frontend/                 React 对话工作台
docs/                     共用说明、设计和验证记录
```

依赖关系：前端通过 DSH 与 Agent 对话，Agent 插件通过 stdio RPC 调用 Python 后端。`sgz_agent` Python 包名保持不变，插件的 `rootDir` 仍为仓库根目录 `D:\agent`。

## 启动与开发

以下命令从仓库根目录运行：

```powershell
# 初始化或同步后端环境，依赖安装到根目录 .venv
powershell -ExecutionPolicy Bypass -File backend\scripts\Sync-Backend.ps1

# 启动已安装插件的 DSH
powershell -ExecutionPolicy Bypass -File agent\scripts\Start-Agent.ps1

# 后端测试、插件构建与测试（不操作游戏）
powershell -ExecutionPolicy Bypass -File agent\scripts\Test-Agent.ps1

# 前端开发服务
npm --prefix frontend run dev
```

后端配置使用 `backend/config/game.local.json`，新机器可从同目录的 `game.example.json` 复制并校准。详细入口见 [Agent 说明](agent/README.md)、[后端说明](backend/README.md)、[前端说明](frontend/README.md)。

## 本地环境与数据

`.venv/`、`.state/`、`.tools/` 为忽略提交的本地环境、运行数据和工具，继续保留在根目录。数据库、知识索引和校准图仍使用原路径；`backend/config` 中的 `data_dir` 通过 `../../.state` 指向它们。不要另建一份运行数据来代替已有任务记录。

## 已安装 DSH 插件的迁移

目录调整后，旧插件的配置和 skills 路径需要更新。完成当前任务并完全退出 DSH 后，执行：

```powershell
npm --prefix agent/plugin run build
Push-Location agent/plugin
npm pack --pack-destination ../../.state/package
Pop-Location
powershell -ExecutionPolicy Bypass -File agent\scripts\Install-DshPlugin.ps1
powershell -ExecutionPolicy Bypass -File agent\scripts\Start-Agent.ps1
```

安装脚本继续备份 DSH 包管理元数据。迁移不自动中断正在运行的 worker；新布局的实机集成需在插件更新后核验。
