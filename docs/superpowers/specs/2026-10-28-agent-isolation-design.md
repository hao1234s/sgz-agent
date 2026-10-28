# Agent 功能隔离设计

## 目标与范围

用户已选择完整模块隔离：纯会话独立运行，Windows 游戏客户端能力与向量知识库分别按需启用。保留现有功能、工具名称、任务记录和知识索引。

本次调整代码结构、依赖声明、配置、工具注册和启动脚本；不部署云端、不操作游戏、不新增远程客户端连接，不迁移数据库或更换向量模型。

## 方案比较

- 只移动文件：迁移较小，但无法消除纯会话对 Windows worker 的运行依赖。
- 按能力隔离：目录、启动、依赖与提示词同时划分，可验证模块独立运行。用户已选择此方案。
- 拆成独立仓库和发布产品：增加版本协调成本，当前不采用。

## 目标结构

```text
agent/plugin/src/
  index.ts                 插件装配与配置兼容
  core/                    公共工具类型、注册辅助、worker 通信
  conversation/            技能目录与加载、纯会话提示词
  analysis/                无客户端依赖的战前计算工具
  client/                  游戏工具定义、提示词、客户端 worker 生命周期
  knowledge/               知识工具定义、提示词、知识 worker 生命周期
agent/skills/
  conversation/            无客户端依赖的技能
  client/                  实机操作技能
agent/profiles/            chat、knowledge、local 三种配置模板
backend/src/sgz_agent/
  core/                    共享 RPC 协议等无平台依赖基础设施
  analysis/                combat_assessment 等纯计算
  client/                  Windows、OCR、接口适配、业务调度及客户端存储
  knowledge/               资料来源、向量索引、检索和独立 CLI
  <原入口或模块>           有必要时保留薄兼容入口
backend/config/
  client/                  客户端配置模板与知识无关配置
  knowledge/               知识导入来源配置
```

前端继续负责消息、流式会话和设置；现有云端部署仍使用 DSH CLI，不引入游戏后端。具体小模块名称可由实现者按实际依赖决定，不能只改目录而保留反向依赖。

## 依赖边界

conversation 不导入 client 或 knowledge；core 不导入任何能力模块。analysis 只依赖标准库或 core，不读取游戏配置、不访问桌面、不创建客户端服务。client 与 knowledge 可依赖 core，各自独立，不能互相导入。客户端工作流需要知识时由 Agent 通过知识工具组合。

Python 基础安装不包含截屏、OCR、Frida、向量模型。按 desktop、ocr、api、knowledge 等 extras 安装；逐项核对现有运行时依赖，避免只重命名 extras。跨平台能力独立选择 Python 路径，不硬编码 Windows 解释器作为所有平台默认值。

## 配置与启动

提供 chat（仅会话）、knowledge（会话加知识库）、local（原有完整功能）模板。新模板显式列出能力开关，关闭的能力不注册工具、不注入专属提示词、不创建 worker，不读取该能力配置。

为兼容已安装插件，未提供新能力配置时沿用现有完整功能；显式配置后仅启用所选能力。原 autoStart 只作用于已启用客户端模块。知识库仍懒启动，客户端启动失败不影响会话、技能或纯计算。知识 worker 故障不阻断客户端工具。

sgz_assess_combat 保持原工具名称和结果语义，改走独立的纯计算入口，不经 AgentService 或客户端配置。技能 provider 和原技能名称保持不变，按配置暴露对应技能目录，并更新技能加载结果中的目录信息。

## 迁移与兼容

更新仓库内 imports、JS 资源路径、package-data、脚本、测试与文档。旧 Python CLI 和必要的导入入口保留兼容转发；兼容模块应保持现有调用和测试可用，避免因 monkeypatch 对象变化引入隐蔽回归。

现有 .state 下数据库、知识目录、租约和 request_id 去重记录原地保留。现有本机 game.local.json 不覆盖；旧配置路径和显式 --config 继续可用，新模板采用新路径。现有安装配置由迁移说明及安装脚本支持，不修改运行中的 DSH 或终止现有 worker。

## 验收

1. chat 配置在无游戏配置、无 Windows 库、无向量库时仍可加载插件和技能，不启动 Python worker。
2. 纯计算可在不加载客户端模块的情况下执行；工具名、输入和结果保持兼容。
3. knowledge 配置不注册游戏工具，知识检索不加载客户端模块。
4. local 保留现有工具、并发与超时策略、停止机制和客户端安全检查。
5. 模块依赖检查覆盖禁用能力不加载、反向导入、按能力工具和提示词注册。
6. 后端回归、插件构建及测试、前端相关检查通过；不做实机游戏动作。

## 实现者可决定的事项

保持一个仓库和一个 Python distribution，使用子包与 extras 隔离；暂不拆发布包。优先维护清晰依赖边界，再控制兼容层数量。脚本保留原入口，可增加按 profile 选择方式。所有变更留在工作区供用户审阅，不自动提交或部署。
