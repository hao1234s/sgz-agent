# 三国志·战略版 DSH Agent

会话、纯计算、知识库和 Windows 客户端按能力装配。新模板位于 `profiles/`；不提供新能力配置的旧安装仍启用全部功能。

## 能力配置与安装

| 模板 | analysis | knowledge | client |
| --- | --- | --- | --- |
| chat | 开，首次计算启动 | 关 | 关 |
| knowledge | 开，首次计算启动 | 开，首次检索启动 | 关 |
| local | 开，首次计算启动 | 开，首次检索启动 | 开，按 autoStart 启动 |

`capabilities: {}` 表示只保留会话/技能工具；显式对象中未列出的能力为关闭。禁止的能力不加载实现、不注册工具、不注入专属提示词，也不扫描该能力的技能目录。chat 普通会话不要求 Python，计算时才要求基础 Python 包。

```json
{
  "rootDir": "/absolute/path/to/sgz-agent",
  "capabilities": {"analysis": true, "knowledge": true, "client": false},
  "analysis": {"pythonPath": "/path/to/base-env/bin/python"},
  "knowledge": {"pythonPath": "/path/to/knowledge-env/bin/python", "directory": ".state/knowledge"}
}
```

解释器优先使用模块自己的 `pythonPath`，其次旧全局 `pythonPath`，最后为根 `.venv/Scripts/python.exe`（Windows）或 `.venv/bin/python`。`client.configPath`、`client.autoStart` 分别优先于旧全局字段。关闭 client 时 autoStart 不产生作用。客户端启动失败不影响技能、计算和知识；知识失败不阻断客户端。

显式能力配置启动时，插件也会在当前 DSH 运行实例内收窄 `sgz-filesystem` 的原生扫描目录，防止遗留 provider 配置暴露禁用技能；该修正不改写宿主配置文件。修改能力时仍建议同时更新插件和原生技能 provider。请从同一模板生成配置，避免只改工具开关而留下旧扫描目录：

```sh
npm --prefix agent/plugin run build
node agent/scripts/Render-Profile.mjs --profile chat --root /absolute/path/to/sgz-agent --output /tmp/sgz-chat.patch.yml
# --no-auto-start 可使 local 的客户端也按需启动
```

Windows 在维护窗口完整退出 DSH 后执行：

```powershell
powershell -ExecutionPolicy Bypass -File agent/scripts/Install-DshPlugin.ps1 -Profile chat
powershell -ExecutionPolicy Bypass -File agent/scripts/Start-Agent.ps1
# local 按需启动客户端：安装时加 -NoAutoStart
```

安装器按 profile 生成独立 tarball，备份 DSH 配置与包元数据，并记录 `.state/installed-agent-profile.json`。启动器默认使用安装记录；不会把另一个已安装 profile 偷换成命令行选项。旧安装无记录时按 local 处理。能力切换下次启动生效，不热切换，不终止当前 worker。

回退时在 DSH 退出后恢复备份中的 profile 配置/包元数据并重新安装原插件包；恢复旧的 `installed-agent-profile.json`（原来没有时移除新标记）。保留 `.state` 数据、旧 request_id 和 unknown 状态，不删库或重建索引。现有 `backend/config/game.local.json` 优先，新模板位于 `backend/config/client/game.example.json`。

## 隔离验证

```sh
npm --prefix agent/plugin run build
npm --prefix agent/plugin test
node --expose-internals agent/scripts/Test-Profiles.mjs --runtime /path/to/dsh/package.json
# 加 --python /absolute/path/to/python 可验证实际计算和临时知识库 worker
```

该脚本只在临时目录启动工具、技能与系统提示服务，覆盖三种 profile；不加载模型提供商、不访问游戏或生产数据库。无 `--python` 时验证缺少 Python 仍可启动；指定 Python 时须已安装后端基础包与 knowledge 依赖。`--runtime` 指向固定 DSH 0.2.1-rc.3 runtime 的 package.json。Windows 客户端与 PowerShell 安装流程仍需本机验收。

## 客户端功能与历史验证


Windows 客户端自动化原型，使用 DeepSeek Harness 工具插件、Python 执行器和本地 PaddleOCR。已实机跑通手机扫码后的身份核验、游戏内坐标查询、地块详情读取、坐标二次确认与导出。3×3 区域共 9 个点全部完成，暂停恢复后没有重复记录。

手机扫码和授权由用户完成。已登录核验路径已实测；二维码失效后的自动刷新仍需校准。派兵和补兵只在对应客户端能力核验通过时可用，具体以工具返回的 capabilities 为准。

接口快速扫描已接通：0.5.0 复用已登录客户端的地图数据和服务器个人贼寇查询，不需要跳地图。实测 2500 格约 1.53 秒。资源占领状态未知，贼寇范围限当前角色个人列表。详见 [接口格式与核对记录](../docs/protocol-investigation.md)。

## 在 DSH 中使用

`dsh-sgz-agent` 使用本机 DSH desktop profile。重新打开 DSH 后可以输入：

> 查看游戏状态，读取配置的 world，然后探索坐标 (591,830) 到 (593,832)，完成后导出 JSON 和 CSV。只查看地图，不派兵。

先调用 `sgz_game_status` 取得 `world`，探索请求必须使用该值，避免跨区服、赛区或角色混写数据。游戏应保持前台，客户区大小保持 **1264×711**。运行中移动或缩放窗口会停止当次动作。

| 工具 | 用途 |
| --- | --- |
| `sgz_game_status` | 查看窗口、任务、校准状态和 world |
| `sgz_prepare_login` | 建立登录任务；已登录时核验角色编号、服务器和赛区 |
| `sgz_observe_map` | 读取当前地块坐标及可见详情 |
| `sgz_inspect_target` | 查看一个坐标，连续两次确认落点 |
| `sgz_explore_region` | 蛇形浏览矩形区域，最多 25 点 |
| `sgz_pause_task` | 停止新输入并保存断点 |
| `sgz_resume_task` | 显式恢复，重新核验身份并跳过已保存点 |
| `sgz_export_results` | 导出本地 JSON 或 CSV |
| `sgz_knowledge_search` | 检索武将、战法、基础机制，返回来源及版本说明 |
| `sgz_knowledge_status` | 查看知识条数、覆盖范围、模型和索引状态 |
| `sgz_protocol_status` | 检查客户端接口依赖、身份绑定和版本；连接在扫描时验证 |
| `sgz_replenish` | 接口补满指定/全部城内闲置普通队伍；只读预览和执行能力分别由 `read_available` / `available` 报告 |
| `sgz_replenish_reconcile` | 两次只读观察对账旧补兵命令，保留历史未知结果；只允许满足严格兼容条件的新目标扩展 |
| `sgz_scan_region` | 默认 `source=api` 快速读取区域资源及个人贼寇；显式 `source=ui` 才逐格采集 |
| `sgz_search_map` | 检索已观察的资源/贼寇，按区域、种类、等级、时效分页 |
| `sgz_refresh_roster` | 刷新自己的主城/分城与队伍快照；需要本机页面校准 |
| `sgz_query_roster` | 查询城池、队伍当前坐标、目的地、状态和数据完整性 |
| `sgz_assess_combat` | 通用战前评估：逐将兵力、到达士气、同配置完整战报；仅计算输入，不派兵、不输出虚构胜率 |
| `sgz_hunt_start` | 明确启动多队巨戟士持续任务；实际能力不足时拒绝启动 |
| `sgz_hunt_status` | 查看任务、每队阶段、命令对账和客户端能力缺口 |
| `sgz_hunt_stop` | 持久化暂停新调度，不撤回正在交战的队伍 |
| `sgz_hunt_resume` | 用户明确继续后恢复，先对账未确认命令 |
| `sgz_skills_list` | 列出本项目可用技能摘要 |
| `sgz_skill_load` | 按需加载本项目 SKILL.md 正文与资源基目录 |

## 区域检索与队伍位置

例如向 DSH 说：“检索 (591,830) 到 (593,832) 范围内 4～6 级铁矿。”插件先检查 `sgz_protocol_status`，再用 `sgz_scan_region` 快速读取，任务完成后 `sgz_search_map` 筛选。API 单次最多 2500 格，坐标 1..1620，无需窗口前台。资源为地图配置，贼寇为个人列表；占领状态和完整世界怪物覆盖未知。每次新扫描用新 request_id。显式 `source=ui` 才进入原画面流程，每轮最多 15 分钟，可恢复；`sgz_explore_region` 保留原 UI 行为及 25 点限制。

`sgz_search_map` 接受 world、x0/y0/x1/y1，边界包含在内；可选 kind（any/resource/bandit）、resource（铁矿/木材/石料/粮食）、min_level/max_level、max_age_seconds（默认 3600）、include_stale（默认 false）、limit（1～100）、offset。按 y/x 稳定分页，返回 total 和 next_offset。每格先取最新观察，再做筛选，不会重新返回已经消失的旧目标。coverage 标明未采集点，空结果不能解释为整个区域没有目标。

`sgz_query_roster` 支持 city_id、city_kind（main/sub）、status，默认 300 秒后标记过期。coordinate 为当前位置，destination 为行军目的地；未知位置为 null。请同时检查 observed、complete、position_complete、stale、refresh_required 与 refresh_error。缺少主城/分城/队伍页面校准时，`sgz_refresh_roster` 会明确报错，不能把尚未采集解释为“没有队伍”。

skill 系统接入 DSH 原生目录发现、摘要、按需加载和热更新。已添加 [巨戟士讨伐 skill](skills/client/sgz-giant-halberdier/SKILL.md)：只打指定队伍和巨戟士；战前核验预计到达士气，交战及战平续战期间不检索、不检查兵力；整次交战结束后低于 20000 才回城或可用营帐补兵，达标时自动处理已授权的“坚持出征”。包括回城/调动区别、伤兵恢复和征兵核验，以及带日期的机制来源。它是 agent 的执行流程；客户端动作必须使用当前已注册、经能力核验的工具。详见 [skill 系统](../docs/skill-system.md)。

新增 [多队持续打野 skill](skills/client/sgz-multi-army-hunt/SKILL.md) 与持久化调度器：默认选主城/分城已编成普通队伍，排除空槽和特殊队伍；每队实际位置周围 70 格圆形候选范围，每 120 秒检查阶段。战中不查该队兵力、不为该队检索、不撤回；整次结算后才检查。优先回当前调动驻点，没有则回所属城；营帐低兵普通征兵，主城/分城补到当前上限。比较直达与“调动＋驻点准备＋最终讨伐路程”的实际预览时间，选择士气、权限和容量均符合的更快路线。任务、全局目标预占和发送前命令日志保存在 `.state/hunts.sqlite3`；结果未知不重发。

**当前多队实机自动打野尚未启用。** 2026-10-25 只读接口校准出现客户端崩溃，隔离记录为 `.state/hunt-client-quarantine.json`。随后获准的一次受控测试成功读取状态，但路线预览解码校验失败并立即退出；客户端未再次崩溃，仍不足以解除隔离。调度、回补与并发规则通过离线测试；完整 70 格实时覆盖、整次交战结束判定、调动路线士气/驻点资格及派兵补兵执行还需实机验收。所有生产接口注入均受隔离约束，`sgz_hunt_status` 会明确返回 `available=false`；不能把工具已注册理解为可派兵。详见 [客户端接口证据与缺口](../docs/hunt-client-contract.md)。

[通用战斗评估 skill](skills/conversation/sgz-combat-assessment/SKILL.md) 独立于任何武将组合，通过 `sgz_assess_combat` 输出士气伤害系数、逐将兵池及社区近似输出指标，并核对同规则/配置/完整守军战报。兵力近似公式不会决定胜负；完整证据不足会明确返回 `insufficient_evidence`，胜率保持未知。当前没有完整战报自动采集和战斗模拟器。公式、微信玩家来源、输入契约和适用边界见 [评估说明](skills/conversation/sgz-combat-assessment/references/model.md)。

巨戟士属于普通地图贼寇，和个人蛮族、木牛流马分开。已核对的等级、名称别名及配置编号见 [贼寇分类](../docs/bandit-classification.md)。当前通用扫描范围仍以 `sgz_protocol_status` 为准。

## 游戏知识库

知识库采用本地 **SQLite + Qdrant + BGE-small-zh-v1.5（512 维）**，与游戏窗口和地图观察数据独立。问游戏知识时使用 `sgz_knowledge_search`，按武将／战法名称精确查找，也支持中文语义检索。示例：

> 查询曹操的兵种适性和自带战法，并给出来源。哪些战法能清除负面状态？计穷和缴械有何区别？

工具可传 `kind: general / tactic / mechanic`、`season`、`limit: 1..10`。赛季按源站已注明的值筛选，未知赛季不会匹配指定赛季。结果中的排序分数不是置信度；每条有 `source_url`、抓取日期、来源类别、赛季、版本核验状态和 `caution`。

首批数据来自官网**基础武将库、战法库**，机制为带出处的简短事实摘要。不是全赛季完整数据；官网未注明的数值等级、更新时间保持未知，不可直接当作满级、最新版。机制文章为 2019 年官方社区攻略，标为历史来源。采集日期不等于游戏数据更新日期。详情和实测见 [知识库报告](../docs/knowledge-report.md)。

调动/营帐/征兵新增 11 条持久化知识：8 条带出处的机制与 3 条用户策略，来源包为 `backend/config/knowledge/knowledge-logistics.json`。已完成实际导入和向量索引，重新打开知识库后 11 条均可检索，重复导入不产生重复项。历史攻略与 2025/2026 年更新分别注明适用边界；[调动补兵知识报告](../docs/knowledge-logistics-report.md) 记录来源和维护方式。

```powershell
# 已安装 knowledge 依赖后，可独立于游戏使用
.venv\Scripts\python.exe -m sgz_agent.knowledge_cli status
.venv\Scripts\python.exe -m sgz_agent.knowledge_cli search --query "清除负面状态" --kind tactic --limit 5
# 默认复用下载缓存，可中断后续跑；--refresh 重新请求官网
.venv\Scripts\python.exe -m sgz_agent.knowledge_cli crawl
.venv\Scripts\python.exe -m sgz_agent.knowledge_cli index
```

知识数据在 `.state/knowledge/records.jsonl`，字段存储在 `knowledge.sqlite3`，向量在 `qdrant/`，模型缓存位于 `models/`，采集覆盖及失败清单为 `crawl-report.json`。数据库支持同一进程内连续查询；运行采集或索引维护前先退出 DSH，以释放独立知识 worker 的文件锁。首次使用模型需要下载，之后可复用缓存。查询仅返回资料，网页文字不作为执行指令。

同一操作重试应复用 `request_id`，改变参数须使用新 ID。原地图探索任务重启后保持暂停；多队持续任务保留原运行/暂停状态，恢复运行前先核验能力并对账。插件退出或超时不会盲目重发游戏命令。

## 核验方式与停止

API 扫描通过本机客户端适配器执行固定只读调用，复用当前会话，无需操作画面。UI 检查和队伍读取仍使用截图、鼠标及数字键盘，沿游戏自带页面操作。不控制手机。

任务先打开基础设置，读取并核验角色编号、服务器和赛区，然后返回地图。输入坐标后，两个输入框读回一致才点击“前往”；落点坐标连续两次一致才保存对应截图。看不清、矛盾或缺失的信息保持未知。

已校准动作比较按钮文字与固定页面控件，避开飘雪、部队动画和半透明名字栏的背景变化；未校准输入仍要求全画面一致。读取地块时，在同一截图内另行核对角色名字的两个文字区域：每个区域必须有唯一、置信度至少 0.95 且文字完全一致的结果。任务启动和恢复时仍核验角色编号、服务器和赛区。OCR 结果在点击前绑定到新截图。坐标标点不清时，可对同一图像做对比度处理后重新识别；不会把小数点直接替换为逗号，原始截图保留不变。

按 **Ctrl+Alt+F12** 请求停止。监听独立于 OCR，鼠标和修饰键会尝试释放；单个按键释放失败不阻止其他键释放，并保留失败键供关闭时重试。热键注册失败则不进入输入状态。

常见暂停原因：`focus_lost` 游戏失焦、`activation_denied` Windows 拒绝切换前台、`geometry_changed` 窗口移动或缩放、`screen_changed` 画面变化、`entry_coordinate_mismatch` 输入框读回不一致、`coordinate_mismatch` 落点不一致、`identity_mismatch` 身份不一致、`map_role_mismatch` 地图角色名字不符或无法确认。先排除原因，再恢复。

这是限定客户端和布局的原型。新版本、分辨率、字体或皮肤需要重新校准；Windows 检查与输入之间仍有微小竞态，强制杀进程不能替代正常暂停。不保证游戏不会检测到自动化。

## 文件与验证

项目位于 `D:\agent`，游戏为 `E:\sgzzlb\S3Client.exe`。

- `backend/config/game.local.json`：身份、世界标识及本机校准，不提交 Git。
- `.state/calibration/`：校准图；`.state/evidence/`：地图证据。
- `.state/agent.sqlite3`：任务与观察；`.state/exports/`：JSON/CSV 导出。
- `.state/package/dsh-sgz-agent-0.6.1.tgz`：含多队调度工具、区域查询、队伍快照、通用战前评估、窗口托管和原生 skill 接入；项目 Python 环境包含原生回调清理修复。实机执行能力以 `sgz_hunt_status` 为准。
- `.state/dsh-profile-backup-20261025-101849/`：安装前包管理元数据备份，未读取或修改凭据。

```powershell
cd D:\agent
powershell -ExecutionPolicy Bypass -File agent\scripts\Start-Agent.ps1
powershell -ExecutionPolicy Bypass -File agent\scripts\Start-Agent.ps1 -Mode doctor
powershell -ExecutionPolicy Bypass -File agent\scripts\Test-Agent.ps1 -DshExecutable 'D:\deepseek\DeepSeek Harness.exe' -Installed
```

第一条启动 DSH，local 默认由插件自动启动唯一客户端 worker（计算和知识各自懒启动），并检查进程；第二条检查窗口及快捷键；第三条运行 Python/Node 测试、构建插件，并从用户安装目录加载插件进行独立检查，不调用模型 API。运行独立集成检查前先退出 DSH，避免与正在运行的 worker 争用数据库。手动截图可用 `agent\scripts\Start-Agent.ps1 -Mode capture -Activate`，输出 `.state/capture.png`。认证页截图请用完删除；后台不会把它归档为地图证据。

`sgz_prepare_login` 托管启动或复用游戏进程，恢复最小化窗口、调整为已校准的 1264×711 客户区并取得前台，再核验角色身份。地图扫描、队伍刷新也会在任务开始时恢复窗口。独立检查窗口可运行 `agent\scripts\Start-Agent.ps1 -Mode prepare-client`。操作后指针会停靠标题栏，避免游戏绘制的鼠标遮住 OCR 文字。

这仍使用当前交互桌面的截图和输入；采集期间需要游戏前台，用户切换窗口会使任务安全暂停。服务常驻不等于客户端可以在被遮挡或最小化时持续执行输入；真正隔离的后台运行需要单独桌面或虚拟机，目前未实现。检索已存数据和加载 skill 不需要游戏前台。游戏若以更高权限运行，Windows 可能拒绝输入；本次实测使用用户重新启动后的普通权限游戏，没有修改安全设置。

服务由 DSH 插件管理，不要同时手动启动另一份 `serve`。数据目录和游戏窗口均有互斥保护。

## 重新安装与开发

需要 Windows x64、Python 3.11、Node 22 或更新版本，依赖已锁定：

```powershell
powershell -ExecutionPolicy Bypass -File backend/scripts/Sync-Backend.ps1 -Profile local -Dev
Push-Location agent/plugin
npm ci
npm run build
npm pack --pack-destination ../../.state/package
Pop-Location
```

本机 uv 在 `.tools\uv\uv.exe`。首次 OCR 会下载 PP-OCRv5 mobile 模型，之后本地推理。不要额外安装其他 OpenCV 发行包。

新机器可复制 `backend/config/client/game.example.json`，但模板不含账号和图像校准，默认不会执行地图动作。安装插件前先完全退出 DSH，再运行 `agent\scripts\Install-DshPlugin.ps1`；它调用 DSH 自带管理器并备份包管理元数据。

区域检索、队伍和托管功能实测见 [验证报告](../docs/map-search-report.md)。早期坐标导航记录见 [P0 报告](../docs/p0-report.md)。
