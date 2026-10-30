# 三国志 Agent 工作台

按能力装配 DSH Agent：会话、纯计算、Windows 游戏客户端、向量知识库分别隔离，前端通过 DSH 提供流式会话与设置。

| profile | 能力 | 启动行为 |
| --- | --- | --- |
| `chat` | 会话、技能、战前计算 | 无 Python 也可启动；首次计算才启动独立 worker |
| `knowledge` | chat + 知识检索 | 计算和知识 worker 分别懒启动 |
| `local` | 完整客户端、计算、知识功能 | 客户端按 autoStart 启动，其余懒启动 |

```text
agent/plugin/src/        core、conversation、analysis、client、knowledge
agent/skills/            conversation 与 client 技能
agent/profiles/          chat / knowledge / local 配置模板
agent/scripts/           profile 渲染、安装、启动和验证
backend/src/sgz_agent/   core、analysis、client、knowledge；旧入口兼容
backend/config/          client 配置模板、knowledge 来源包
frontend/                React 对话工作台
docs/                    设计、计划和验证记录
```

插件不提供 `capabilities` 时保留原完整功能；显式配置后只启用选中的能力。各能力拥有独立进程和错误状态，客户端不可用不阻塞会话、技能、计算或知识库。

从仓库根目录构建与生成配置：

```sh
npm --prefix agent/plugin ci
npm --prefix agent/plugin run build
npm --prefix agent/plugin test
node agent/scripts/Render-Profile.mjs --profile chat --root /absolute/path/to/sgz-agent --output /tmp/sgz-chat.patch.yml
```

Windows 安装在退出 DSH 后显式进行，安装脚本会生成所选 profile 的包并备份配置：

```powershell
powershell -ExecutionPolicy Bypass -File agent/scripts/Install-DshPlugin.ps1 -Profile chat
powershell -ExecutionPolicy Bypass -File agent/scripts/Start-Agent.ps1
```

已有 `.state/`、数据库、知识索引和本机配置原地保留。现有 `backend/config/game.local.json` 优先使用；新机器从 `backend/config/client/game.example.json` 创建同目录 `game.local.json`。详见 [Agent 配置与迁移](agent/README.md)、[后端依赖与测试](backend/README.md)、[前端说明](frontend/README.md) 和 [隔离实施记录](docs/superpowers/plans/2026-10-30-agent-isolation.md)。

[云端部署](agent/deploy/README.md)继续使用独立 DSH CLI、前端与 Nginx；功能隔离不向云端加入游戏服务。
