# Agent 功能隔离实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: 实施时使用 `superpowers:executing-plans` 逐项推进。步骤使用 checkbox 跟踪。用户已批准实施；变更保留在工作区，不自动提交或部署。

**Goal:** 让会话、纯计算、Windows 客户端和知识库按能力装配，保留既有工具契约、任务记录与知识索引。

**Architecture:** 保留一个仓库和一个 Python distribution。插件入口只负责配置兼容与装配；Python 用 `core / analysis / client / knowledge` 子包隔离实现。计算、客户端、知识库分别拥有 worker，禁用模块不加载、不注册、不读取配置。

**Tech Stack:** Python `>=3.11,<3.12`、setuptools/uv、TypeScript/Node、DSH/Cordis、PowerShell；前端继续 React/Vite。

**Spec:** [2026-10-28-agent-isolation-design.md](../specs/2026-10-28-agent-isolation-design.md)

**本轮确认：** chat 保留 `sgz_assess_combat`，调用时才启动独立 Python 进程。启动插件、普通会话、发现及加载技能不需要 Python 环境。

## 全局约束

- conversation 不导入 client 或 knowledge；core 不导入任何能力模块。analysis 只依赖标准库或 core。
- client 与 knowledge 不能互相导入；客户端需要知识时由 Agent 组合工具。旧客户端 RPC 的计算分支可延迟复用 analysis；常规客户端启动不加载 analysis。
- 保持工具名称、输入及结果语义、技能名称和 `sgz-filesystem` provider 不变；保留旧 Python CLI 与必要导入入口。
- `.state`、数据库、知识模型与 collection、租约、request_id 去重记录原地保留；不覆盖现有 `game.local.json`。
- 新能力配置缺省时保持完整功能；显式提供能力配置后只启用所选项。旧 `autoStart` 仅影响已启用的 client。
- 不操作游戏，不新增远程客户端连接，不部署云端，不修改运行中的 DSH，不终止既有 worker；变更留在工作区审阅。

## 现状与实施决策

| 当前实现 | 迁移时必须解决的问题 |
| --- | --- |
| `agent/plugin/src/index.ts` 集中定义全部工具、统一提示词与进程路由 | 按能力拆工具、提示词和生命周期，不能仅拆文件 |
| `sgz_assess_combat` 等待客户端 startup，再调用 `AgentService` | 直接进入独立 analysis worker，避免创建桌面、租约和客户端数据库 |
| `knowledge_cli.py` 已独立启动，向量模型懒加载 | 保留独立进程及既有 RPC 行为，不重写检索算法 |
| Python 基础依赖含 mss/Pillow；代码直接使用未直接声明的 portalocker/numpy | 分拆 extras 并补齐直接运行依赖，不借用传递依赖 |
| `cordis.patch.yml` 扫描整个 `agent/skills` | 必须同步隔离原生 provider 扫描范围，插件结果过滤不能代替此项 |
| worker 路径均为 `.venv/Scripts/python.exe`；启动脚本要求恰有一个客户端 worker | 按模块、平台解析解释器；chat/knowledge 启动不等待客户端 |
| 旧模块测试大量 monkeypatch；JS 通过 `__file__` 同目录读取 | 保持被 patch 的对象身份、资源相对位置和 wheel 包含内容 |

沿用设计中的“按能力隔离”：只移动文件无法消除运行依赖；拆多仓库/多发行包会增加版本协调，本轮不采用。实施按下面的依赖顺序推进，每步有独立验收点。

## 配置契约与能力矩阵

插件新增 `capabilities?: {analysis?: boolean; client?: boolean; knowledge?: boolean}`；conversation 始终存在。

| profile | conversation | analysis | knowledge | client | 插件启动时进程 |
| --- | --- | --- | --- | --- | --- |
| chat | 开 | 开 | 关 | 关 | 无；首次计算启动 analysis |
| knowledge | 开 | 开 | 开 | 关 | 无；计算、知识分别按需启动 |
| local | 开 | 开 | 开 | 开 | client 按 autoStart；另两者按需 |
| 旧配置：无 capabilities | 开 | 开 | 开 | 开 | 保持原客户端自动启动策略 |
| 显式 `capabilities: {}` | 开 | 关 | 关 | 关 | 无，且不注册计算工具 |

- 显式配置中未出现的能力为 false；未知字段或非布尔值应报配置错误，不能退回“全开”。
- 新增 `analysis?: {pythonPath?: string}`、`client?: {pythonPath?: string; configPath?: string; autoStart?: boolean}`、`knowledge?: {pythonPath?: string; directory?: string}`。
- 解释器优先级：模块 `pythonPath` → 旧全局 `pythonPath` → 根目录 `.venv` 的平台路径（Windows 为 `Scripts/python.exe`，其他平台为 `bin/python`）。仅启用/调用该模块时检查依赖；chat 启动不探测 Python。
- client 配置优先级：模块 `configPath` → 旧全局 `configPath` → 已存在的旧 `backend/config/game.local.json` → 新 `backend/config/client/game.local.json`。显式路径失败直接报错，不换配置。两份都存在时旧路径优先，避免悄悄切换身份。
- `client.autoStart` → 旧全局 `autoStart` → true；knowledge 默认目录仍为仓库根 `.state/knowledge`。
- 不支持热切换能力；新配置下次启动生效。worker 错误归属对应能力，不污染其他能力的 startup/error 状态。
- analysis 禁用时隐藏依赖它的计算技能；client 技能对 analysis/knowledge 工具的引用须判断可用性，不能用开启 client 隐式开启其他能力。

## 文件与依赖归属

| 目标 | 文件与职责 |
| --- | --- |
| `agent/plugin/src/core/` | `types.ts` 公共 Context/Skill/Tool 类型；`config.ts` 纯配置归一化；`register.ts` 公共注册辅助；`worker.ts` 原通信实现 |
| `agent/plugin/src/conversation/` | `index.ts`、`skills.ts`、`prompt.ts`：技能查询/加载和会话规则，不引用能力实现 |
| `agent/plugin/src/{analysis,client,knowledge}/` | 各自 `index.ts`、`tools.ts`、`prompt.ts`、`worker.ts`：工具 schema、提示词、执行及生命周期 |
| `agent/plugin/src/index.ts` | 校验配置，装配 conversation，条件加载启用模块；`src/worker.ts` 留旧路径 re-export |
| `backend/src/sgz_agent/core/rpc.py` | 无业务 imports 的 stdio 协议与调度；不保留创建 AgentService 的入口 |
| `backend/src/sgz_agent/analysis/` | `combat_assessment.py`、`service.py`、`cli.py`；纯计算与独立 stdio 入口 |
| `backend/src/sgz_agent/knowledge/` | `__init__.py` 保留原 knowledge.py 实现和 globals；`store.py` 为同模块兼容别名；`sources.py`、`cli.py` 分别接收来源与 CLI 实现 |
| `backend/src/sgz_agent/client/` | 原 `cli.py`、`service.py`、config、models、桌面/OCR、transport、业务调度、存储与全部客户端 JS；另建 `rpc.py` 装配 AgentService |
| `agent/skills/conversation/` | 迁入 `sgz-combat-assessment/`，相对 references 同迁；analysis 关闭时不暴露此技能目录 |
| `agent/skills/client/` | 迁入 `sgz-giant-halberdier/`、`sgz-multi-army-hunt/` 及其 references |
| `agent/profiles/` | `chat.json`、`knowledge.json`、`local.json`：显式能力模板，由同一渲染器生成 plugin/provider 配置 |
| `backend/config/` | 新模板位于 `client/game.example.json`；来源包位于 `knowledge/knowledge-{mechanics,logistics}.json`；旧来源路径继续可读 |

client 迁移清单：`bandits, client_api, config, control, game_ui, guard, hunt, hunt_aftercare, hunt_attack, hunt_client, hunt_dispatch, hunt_dispatch_client, hunt_policy, hunt_reconcile, hunt_retarget, hunt_runtime, hunt_search, hunt_store, hunt_trial, lease, map_query, models, native_session, navigation, recognition, replenish, replenish_client, roster, service, store, transport, vision, visual_guard, windows`，加 CLI/RPC。即使某些文件只用标准库，游戏业务类型也不应因此放进 core。所有包的 `__init__.py` 禁止急切导入其他能力。

## Review Focus

1. 显式空/部分能力配置不能退回全部开启；禁用模块不能被静态 imports、配置探测或原生技能目录泄露。归 Task 4、5、6。
2. 客户端启动失败、知识模型缺失、任意 worker 超时或退出，其他能力仍可使用。归 Task 4、6。
3. 配置加深一层、不同 cwd、空格路径、旧/新配置共存不能换数据库或角色。归 Task 3、5。
4. 旧 CLI、旧 import、monkeypatch、JS 资源在打包安装后仍有效。归 Task 2、3、6。
5. 拆分不能改变暂停优先响应、并发互斥、unknown 去重与关闭资源次序。归 Task 1、3、4。

## Task 1：公共协议与纯计算独立入口

**Files:** 新建 `backend/src/sgz_agent/{core,analysis}/__init__.py`、`core/rpc.py`、`analysis/{combat_assessment,service,cli}.py`；修改原 `rpc.py`、`combat_assessment.py`；新增 `backend/tests/test_analysis_cli.py`，复用 `test_rpc.py`、`test_combat_assessment.py`。

**Interfaces:** `AnalysisService.dispatch(method, params) -> dict` 只接受 `assess_combat`；提供 `request_stop()`、`close()`。`python -m sgz_agent.analysis.cli serve` 使用 stdio，不提供/读取游戏 config。`core.rpc.serve_stream(service, source, destination, *, immediate_methods=(), max_workers=4)`；旧 `rpc.serve_stream(service, source, destination)` wrapper 传入原即时方法 `game_status`、`pause_task`。

- [x] 新增失败测试：用全新子进程禁止导入 `sgz_agent.client`、旧 `service/config/lease/windows`、PIL、mss、paddleocr、frida、fastembed、qdrant_client；输入 `{phase: 'draw'}` 得到 `decision == 'wait'`，临时目录无配置、数据库或锁文件。
- [x] 运行 `python -m pytest tests/test_analysis_cli.py -q`，确认失败来自入口缺失。
- [x] 抽取协议与原纯函数；保持 `assess_combat` 原签名、模型常量、验证和输出。旧函数导入仍可用；客户端 RPC 的并发上限、通知、错误码、断连关闭顺序不变。
- [x] 运行 `python -m pytest tests/test_analysis_cli.py tests/test_combat_assessment.py tests/test_rpc.py -q`；应全部通过，包括暂停即时响应。把未知方法和非法参数拒绝加入 analysis 测试。

## Task 2：知识子包与可选依赖

**Files:** 新建 `knowledge/{__init__,store,sources,cli}.py`；迁移原 `knowledge.py`；修改 `knowledge_cli.py`、`knowledge_sources.py`、`backend/pyproject.toml`、`backend/uv.lock`；新增 `backend/tests/test_module_compatibility.py`、`test_dependency_contract.py`，复用全部 `test_knowledge*.py`。

**Interfaces:** `sgz_agent.knowledge.KnowledgeBase` 继续导出同一实现；新 CLI 为 `python -m sgz_agent.knowledge.cli`，原 `python -m sgz_agent.knowledge_cli` 保持参数和退出码。配置与数据目录仍由调用者提供。

- [x] 新增失败测试：新旧 KnowledgeBase 是同一类；patch 旧 `knowledge_cli.KnowledgeBase` 后旧 `main()` 实际调用替身；knowledge 新入口不导入 client 或旧游戏模块。
- [x] 运行 `python -m pytest tests/test_module_compatibility.py tests/test_dependency_contract.py -q`，确认失败。
- [x] 将实现移入子包；不同时留下同名 `knowledge.py` 与 `knowledge/`。旧 CLI/模块使用薄兼容入口，涉及 globals monkeypatch 的入口保证同一模块对象，不能仅 `from ... import *`。保留知识锁、RPC 错误行为和懒加载。
- [x] 基础 `dependencies` 清空；`desktop` 收入 mss/Pillow；`ocr` 显式包含图像/数组处理依赖与原 Paddle 依赖；`api` 保留 Frida；`knowledge` 保留 qdrant-client/fastembed 并显式加入 portalocker。portalocker/numpy 约束从当前 lock 核对确定，不顺带升级现有依赖。
- [x] 更新锁文件，在独立临时环境验证基础安装和 knowledge-only 安装；不对当前共享 `.venv` 执行可能卸载 extras 的同步。
- [x] 运行 `python -m pytest tests/test_knowledge.py tests/test_knowledge_cli.py tests/test_knowledge_sources.py tests/test_knowledge_curated.py tests/test_module_compatibility.py tests/test_dependency_contract.py -q`；向量测试使用现有假 embedder，不下载模型，不访问真实知识目录。

## Task 3：客户端实现迁移与兼容

**Files:** 按上方清单创建 `backend/src/sgz_agent/client/*`，JS 同迁；修改原模块为必要的兼容入口、`__main__.py`、pyproject package-data；修改 `backend/scripts/` imports、JS 路径及测试资源路径；扩充 `test_module_compatibility.py`、`test_cli_entrypoints.py`、`test_config.py`，新增 `test_package_resources.py`。

**Interfaces:** `python -m sgz_agent.client.cli` 为新客户端入口，旧 `sgz-agent`、`python -m sgz_agent`、`python -m sgz_agent.hunt_trial` 保持可用；`AgentService(config, *, desktop=None)` 和业务方法保持签名。旧 RPC `serve(config)` 延迟转发新 client RPC；core 不反向导入它。

- [x] 新增失败测试：旧 `cli.load_config`、`windows.WindowsDesktop`、`client_api.readiness` 的 monkeypatch 对新调用链有效；旧 CLI 在无关 cwd 下 `--help` 成功且不创建状态文件。
- [x] 新增路径用例：旧配置、新目录配置、显式绝对/相对配置、路径含空格；缺省 data_dir 均指向对应仓库根 `.state`，显式 data_dir 仍相对配置文件解释。自定义目录未给 data_dir 保留旧的相对规则，不猜仓库位置。
- [x] 运行上述新测试，确认缺失新模块/路径导致失败，再迁移实现和 JS。新内部 imports 直接指向新包，兼容入口仅服务外部调用；不通过旧入口绕过依赖约束。
- [x] 保留旧客户端 dispatch 的 `assess_combat` 转发以兼容旧消费者，插件后续不再使用这条路。客户端不引入任何 knowledge import。
- [x] 新目录模板使用 `../../../.state`；旧目录与省略 data_dir 的兼容规则分别测试。旧本机配置不搬迁、不改写。pyproject 将 9 个 JS 资源归入 `sgz_agent.client` 的 package-data。
- [ ] 在 Windows 测试环境运行 `python -m pytest tests -q`；保持现有调度、租约、身份核验、停止和 unknown 去重测试通过。Mac/Linux 不以大量 skip 替代此验收。
- [x] 对构建出的 wheel 做独立目录安装验证，确认所有 JS 可读取、旧 CLI help 可用；不能只验证源码 editable install。

## Task 4：插件按能力装配和进程隔离

**Files:** 按文件表拆分 `agent/plugin/src/`；修改 `test/{features,knowledge,layout,worker}.test.mjs`；新增 `test/{capabilities,isolation}.test.mjs`、`test/fixtures/legacy-tools.json`。

**Interfaces:** `resolveConfig(input: Config, platform: NodeJS.Platform): ResolvedConfig` 为纯函数，输出上述完整布尔开关、仓库绝对路径及模块选项；不探测文件，旧/新配置的存在性选择延迟到 client worker。`registerConversation(ctx, options): void` 只使用公共类型与已算出的技能目录；能力模块统一 `register(ctx, config): void`。每个能力模块持有自己的 WorkerClient 和 dispose，保持 `call(method, params, timeout)` 语义。

- [x] 先从当前实现保存工具名、parameters、并发属性的兼容 fixture；新增矩阵测试：`undefined -> 全开`、`{} -> 全关`、`{knowledge:true} -> 仅知识`，非法值报错。
- [x] 新增失败测试：chat 加载及技能 list/load 的 spawn 次数为 0；首次计算只创建一个 analysis worker；knowledge 首次请求只创建知识 worker；client 禁用时 `autoStart:true` 无效。
- [x] 拆开工具描述/schema 和提示词；按启用能力动态 import。建议入口 `apply(ctx, config): Promise<void>`，单测显式 await；用既有 DSH boot 测试先证明宿主等待异步 apply 完成后工具已注册。此契约未通过前不发布，也不以静态加载所有能力规避隔离。
- [x] conversation 只注入技能与数据边界规则；analysis 注入纯计算规则；client/knowledge 分别注入专属规则。跨模块工具引用由装配层按开关提供布尔上下文，禁用知识时客户端提示词不要求调用不存在的知识工具。
- [x] 独立 worker 路由：analysis → `sgz_agent.analysis.cli serve`，knowledge → `sgz_agent.knowledge.cli serve --directory ...`，client → `sgz_agent.client.cli serve --config ...`。analysis 不等待客户端 startup。
- [x] 保留 client 启动超时 15,000 ms，`replenish/hunt_dispatch_start` 3,600,000 ms，其他调用 120,000 ms，原 `isConcurrencySafe` 集合不变。dispose 用互不短路的清理方式，关闭失败不能阻止其他进程清理；不改变客户端超时后的持久化语义。
- [x] 注入客户端 startup 失败、知识进程退出、analysis 超时，逐项验证其他能力仍可调用；缺少解释器仅报对应工具错误，普通会话与技能仍可用。
- [x] 运行 `npm --prefix agent/plugin run build`、`npm --prefix agent/plugin test`；全部通过，旧默认配置与 fixture 契约一致。

## Task 5：技能目录、配置模板及启动安装脚本

**Files:** 移动 `agent/skills/` 三个技能与 references；新增 `agent/profiles/{chat,knowledge,local}.json`、`agent/scripts/Render-Profile.mjs`、`agent/plugin/test/profiles.test.mjs`；修改 `cordis.patch.yml`、`Install-DshPlugin.ps1`、`Start-Agent.ps1`、`Test-Agent.ps1`、`Test-Dsh.cjs`、`Test-SgzSkills.cjs`、`backend/scripts/Sync-Backend.ps1`、`.gitignore` 与配置模板。

**Interfaces:** `renderProfile({profile, rootDir, overrides})` 输出一份 Cordis patch，同时设置 `sgz-agent.config` 和 `sgz-skills.config.customSkillDirs`；命令 `node agent/scripts/Render-Profile.mjs --profile chat --root <仓库绝对路径> --output <临时patch路径>` 只生成文件，不安装或启动。

- [x] 先写渲染器测试：三种 profile 显式列出三个布尔开关；providerName 恒为 `sgz-filesystem`；chat/knowledge 不含 client 目录；analysis 关闭不扫描计算技能；路径含空格可用，不写入固定 `D:/agent`。
- [x] 移动技能后保留名称及相对资源；`sgz_skills_list` 保留原 `directory` 根路径兼容字段，新增 `directories` 表示实际启用目录，`skill_load` 的 `resourceBase.path` 指向实际技能目录。发现和加载都核验目录归属及 modelInvocable，防止旧 provider 配置暴露禁用技能。
- [x] client 技能明确按可用能力引用计算/知识工具；不增加游戏能力或重写策略。旧知识来源配置路径继续可读：当前传入路径可由第三方直接读取，优先保留小型兼容副本并测内容一致，新文档只引用新路径。
- [x] 新增本机配置忽略规则 `backend/config/**/*.local.json`，保留原规则；新模板不含身份和校准数据。
- [x] 安装脚本增加 `-Profile chat|knowledge|local`，默认 local 保持原习惯；从构建产物在临时 staging 目录写入渲染后的 patch 再打包，避免 npm 打包遗漏插件目录外的 profiles。继续拒绝在 DSH 运行中安装，备份实际会被改写的 profile 配置和包元数据。
- [x] 启动脚本根据所选 profile/autoStart 验证：chat/knowledge 不要求客户端 Python 或 worker；local 自动启动时才检查恰好一个客户端 worker，识别旧、新 module 命令行。客户端模式 doctor/capture 等在禁用 client 时明确拒绝，不能偷偷启动。
- [x] Sync-Backend 按 profile 选择 extras：chat 仅基础包（且普通会话可完全不执行同步），knowledge 仅 knowledge，local 为 desktop+ocr+api+knowledge；dev 独立可选。默认 local 不删除现有功能；跨平台文档提供 uv 命令，不要求 Mac/Linux 使用 Windows 桌面启动器。
- [ ] 运行 `node --test agent/plugin/test/profiles.test.mjs`，PowerShell 仅做解析/模拟进程测试；用隔离 DSH fixture 验证原生目录只见启用技能，不连接生产配置或游戏。

## Task 6：回归约束与依赖检查

**Files:** 新增 `backend/tests/test_module_boundaries.py`；扩充 `agent/plugin/test/isolation.test.mjs`、`backend/tests/test_package_resources.py`；更新 `Test-Agent.ps1` 的能力验收选项。

**Interfaces:** 检查覆盖新包静态 import、旧兼容入口解析后的真实归属、运行时加载模块和 spawn 记录；不只搜索目录名称。

- [x] 为每条边界加入 AST/import 检查：core 不导入能力；analysis 无游戏/向量依赖；client/knowledge 互不引用。用内存里的违规 fixture 验证检查器确实会报错，避免检查器永远通过。
- [x] 在全新 Python 子进程中挂阻断 import 的 finder，验证 analysis 与 knowledge 运行期间均不加载 client，也不通过旧兼容模块间接加载；已有模块缓存不能掩盖结果。
- [x] 插件每个 profile 用新 Node 进程运行；通过模块 loader 记录证明禁用能力实现没有加载，结合工具、提示词、技能目录与 spawn 断言。覆盖全部 8 种能力布尔组合。
- [x] 临时环境验证基础 distribution 无 mss/PIL/Paddle/Frida/向量依赖，知识环境仅安装 knowledge extras 即可工作。chat 用不存在的 Python 路径仍能加载与查询技能；调用计算才返回对应依赖缺失错误。
- [x] 构建 wheel 与 npm tarball 后从无关 cwd 验证入口/资源，确认包内不存在 local 配置和状态数据；验证失败应能定位模块或资源，不接触真实 `.state`。
- [x] 将检查接入现有测试入口；运行后文验收矩阵，不通过的能力不得标记迁移完成。

## Task 7：迁移说明与最终交付

**Files:** 更新 `README.md`、`agent/README.md`、`backend/README.md`、`docs/skill-system.md`、`agent/deploy/README.md`；记录验收结果到本计划末尾。

- [x] 文档说明三种 profile、配置优先级、按能力解释器、首次调用行为、extras 安装与旧入口兼容。根 README 保持简短，详细使用说明放各模块 README。
- [x] 迁移顺序写明：完成离线验证 → 准备 profile 安装包与备份 → 在用户选择的维护窗口退出 DSH → 安装及启动核验。此任务只准备材料，不执行用户机器的安装切换。
- [x] 回退使用旧插件包和已备份 profile 配置；保留原 `.state`，不得通过删库、清空 unknown 或重建索引完成回退。
- [x] 运行下面的最终验收；记录实际命令、平台、结果与未执行项，不复用旧计划中的测试数量。前端和部署只检查兼容性，不引入游戏后端或调用 deploy `--apply`。
- [x] 最终检查 diff 仅含预期改动，交付计划对应的实现、测试证据与迁移说明；不自动 commit、push、安装、部署。

## 验收矩阵与执行命令

| 环境 | 必须通过 | 不可替代的证据 |
| --- | --- | --- |
| 无 Python 的 Node 环境 | chat 插件/技能加载；无 worker；禁用模块未加载 | spawn=0，禁用工具/提示词/原生技能缺席 |
| Python 3.11 基础环境，Mac/Linux | 独立计算、CLI、core 协议及依赖边界 | 未安装桌面/向量依赖仍得到兼容计算结果 |
| Python 3.11 + knowledge | 知识 CLI/检索/锁与来源包 | 不加载 client；使用临时索引及假 embedder |
| Windows + local extras | 全后端回归、客户端 CLI/兼容与 DSH 离线集成 | 不连接游戏，验证停止、去重、资源和宿主加载 |
| Node 前端及部署单测 | 前端测试/构建、部署包合同 | DSH 会话协议继续可用，未加入游戏服务 |

以下为验收命令模板，实际执行结果和平台限制见末尾实施记录：

```text
# 后端目录；python 必须指向对应隔离的 Python 3.11 环境
python -m pytest tests/test_analysis_cli.py tests/test_combat_assessment.py tests/test_rpc.py tests/test_module_boundaries.py tests/test_dependency_contract.py -q
python -m pytest tests/test_knowledge.py tests/test_knowledge_cli.py tests/test_knowledge_sources.py tests/test_knowledge_curated.py tests/test_module_compatibility.py -q
python -m pytest tests -q                         # Windows 完整离线回归
uv build --out-dir <临时产物目录>                 # 后续在独立环境安装 wheel 验证

# 仓库根目录
npm --prefix agent/plugin run build
npm --prefix agent/plugin test
(cd agent/plugin && npm pack --dry-run)           # PowerShell 可分步切目录执行
npm --prefix frontend test
npm --prefix frontend run build
python -m unittest discover -s agent/deploy/tests -v
git diff --check
```

## 实施顺序与范围收口

按 **Task 1 → 2 → 3 → 4 → 5 → 6 → 7** 执行。Task 1 完成后纯计算已能独立验证；Task 4 完成后能力开关及故障隔离可验证；Task 5 完成后三种 profile 才有完整的工具、技能和启动闭环。每步保留可审阅 diff，不把目录迁移、行为变化和兼容修复一次性混在最后验证。

本轮不新增 UI 能力开关、远程游戏连接、自动模型下载、自动重启、索引重建或独立发布包。DSH 异步插件装配和原生技能扫描的集成结果是实施验收项；若当前环境没有 DSH/Windows，明确列为待验收，不能用 mock 通过宣称实际宿主已验证。


## 实施记录（2026-10-30）

- 工作分支：`codex/agent-isolation`，变更未提交；保留当前工作区与所有原运行数据。
- Python 已迁移至四个子包，旧入口以模块别名保持 globals/monkeypatch 行为。JS 与客户端同包；内置知识机制种子改为 package resource，解决源码目录变深及 wheel 安装后的路径问题。
- 插件保留 27 个旧工具的 schema 和并发属性。新增 8 种能力组合、禁用模块加载阻断、故障隔离、技能过滤与跨平台解释器测试。
- DSH `0.2.1-rc.3` 原生 boot 已在临时目录验证 chat/knowledge/local 的异步注册和原生技能发现/加载；无 Python 启动与实际 analysis/knowledge worker 调用分别通过。没有加载模型服务、访问游戏或生产数据库。
- 干净基础 wheel 安装不含可选运行依赖；计算、包资源和 6 个新旧 CLI help 在无关 cwd 通过。另建仅安装 knowledge extras 的环境，实际知识状态与计算调用通过，不下载向量模型。
- 当前完整离线补充回归：1027 项通过；macOS 使用临时、未加入产品的 Windows 文件锁替身。这不代表 Windows `msvcrt` 或桌面环境验收通过。
- 插件 26 项测试及 TypeScript 构建通过；前端 18 项测试及构建通过；部署 7 项单测通过。npm tarball / wheel 的模块、9 个 JS 资源及无本机数据打包检查通过；冻结 lock 校验通过。
- 仍待 Windows 原生完整回归、PowerShell 脚本解析/执行和真实桌面安装验收；本轮未执行安装、部署、游戏操作或索引迁移。客户端集成脚本仍需显式授权运行。
- 独立审查提出三项问题，均先复现再修复：旧原生 provider 泄露禁用技能、知识包公开 globals 的 monkeypatch 失效、自定义 config/client 目录默认数据路径变化。修复后补充全套回归 1027 项通过；未进行第二轮独立审查。
- 原生 provider 的修复使用 Cordis 公开 `Fiber.update(config, true)` 运行时收窄 `sgz-filesystem` 的目录与默认根，不持久化改写宿主配置。真实 DSH 的 stale-provider 与空能力配置两种新增场景通过，原配置文件内容保持不变。
- 为保持原知识模块 globals 的对象身份，将原实现放在 `knowledge/__init__.py`，`knowledge/store.py` 指向同一模块；仍只导入标准库，向量依赖和模型保持懒加载。
- 最终 wheel 再安装后，基础包、knowledge-only、旧 globals 替换、新旧 CLI 和包内资源在仓库外验证通过。所有 9 个客户端 JS 与迁移前逐字节一致，31 个客户端 Python 实现仅移动位置。
- 审查未覆盖的 Windows 原生桌面/锁、PowerShell、实际游戏、用户已安装 DSH 状态保持待验收；worker 故障测试覆盖独立传输故障与代表性的跨能力组合，不宣称穷尽所有故障组合。

实施决策：为保留可审阅的未提交改动，在当前 checkout 新建实施分支；没有创建第二份 worktree。知识种子随包分发并保留外部兼容来源文件；旧完整配置保留自定义原生技能目录，显式能力配置严格限定启用目录。Windows 缺席时只补充模拟锁回归，不把结果升级为原生验证。
