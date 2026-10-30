# 三国志项目 skill 系统

复用 DSH 原生 `@deepseek-ai/dsh-skill`、`@deepseek-ai/dsh-skill-filesystem` 和原生 `skill` 加载工具；项目插件提供 `sgz_skills_list`、`sgz_skill_load`，并在系统提示中告知 agent 查询目录与按需加载。已添加 [巨戟士讨伐](../agent/skills/client/sgz-giant-halberdier/SKILL.md)：指定队伍、只打巨戟士，战前考虑预计到达士气；交战、战平续战及同目标连续守军阶段不检索、不查兵力、不触发回城；整次交战最终结束后才检查低于 20000 的回补条件。战前达标时允许自动接受已授权的兵力悬殊提示。调动/返回驻点、伤兵恢复与实际征兵分别核验，地点按总耗时和成本选择；[机制与来源](../agent/skills/client/sgz-giant-halberdier/references/mechanics.md) 区分官方旧版说明、本机观测和策略。打地技能尚未编写。

新增 [通用战斗评估](../agent/skills/conversation/sgz-combat-assessment/SKILL.md)，供任意编制使用；巨戟士技能按需加载它，而不是保存甘宁或其他固定队伍模板。工具 `sgz_assess_combat` 只计算传入数据，区分士气伤害折扣、每将兵池、社区近似兵力指标和同条件完整战报依据。它不模拟全部战法、不自动收集战报、不宣称胜率；详见 [模型与微信玩家来源](../agent/skills/conversation/sgz-combat-assessment/references/model.md)。

[多队持续打野](../agent/skills/client/sgz-multi-army-hunt/SKILL.md) 负责自然语言触发与权限边界，程序层 `HuntCoordinator` 负责 70 格候选检索、120 秒独立阶段检查、目标去重、调动路线和回补状态机。它调用窄客户端适配器，任务与命令记录保存于 `.state/hunts.sqlite3`。战中等待只约束该队；停止调度不撤军；重启先对账，不重复发送结果未知的命令。仅询问功能、加载 skill 或查询知识不会启动任务。

当前生产客户端因稳定性事故处于持久化隔离；执行接口、完整范围覆盖和整次战斗判定尚未验收。工具注册和离线模拟通过不代表实机功能可用，必须以 `sgz_hunt_status.capabilities` 为准，不能用 UI 自动化绕过该隔离。调动/补兵等 11 条知识已从 `backend/config/knowledge/knowledge-logistics.json` 导入 `.state/knowledge` 并建索引，区分历史来源、版本适用性与用户策略；详见 [知识持久化报告](knowledge-logistics-report.md)。

## 能力目录

会话/纯计算技能位于 `agent/skills/conversation/`，客户端技能位于 `agent/skills/client/`。profile 渲染器同步配置插件和原生 provider；显式能力配置启动时还会运行时收窄旧 provider 根目录（不写回配置），保证原生发现/加载和包装工具一致；chat/knowledge 仅扫描会话目录，local 扫描两者。analysis 关闭时不暴露依赖计算工具的技能。技能名与 provider `sgz-filesystem` 不变。

`sgz_skills_list.directory` 保留旧根目录字段，`directories` 列出实际启用的目录；`skill_load.resourceBase.path` 指向迁移后的技能目录。旧配置不提供 capabilities 时兼容已有自定义原生目录，显式能力配置仅允许已启用目录。

## 添加技能

在 `agent/skills/conversation/<name>/SKILL.md` 或 `agent/skills/client/<name>/SKILL.md` 中放入 YAML frontmatter 与正文。文件名必须为 SKILL.md；`name` 使用小写字母、数字及连字符，例如未来的 `sgz-resource-march`，必须提供描述具体触发条件的 `description`。元数据由宿主原生 YAML 解析器读取，不是自制简化语法。

```yaml
---
name: your-skill-name
description: 描述何时使用这个技能
disable-model-invocation: false
user-invocable: true
---
```

frontmatter 后写操作流程。附属说明、数据和脚本放在同目录下，加载结果中的 `resourceBase.path` 是相对资源路径的基准。目录发现只返回摘要；正文在调用 `skill` 或 `sgz_skill_load` 时读取。文件变更由原生 watcher 刷新，无需重启 Python worker。

`disable-model-invocation: true` 的技能不出现在 `sgz_skills_list`，也不能通过 `sgz_skill_load` 读取。`user-invocable` 控制宿主显式用户调用。重复名称按 DSH 原生优先级处理；为避免与其他目录重名，项目技能统一使用 `sgz-` 前缀。

技能是 agent 的任务流程说明，不是可执行权限声明。加入文件不会自动运行脚本，不会增加 Python 动作白名单，也不会产生派兵功能。以后编写战斗技能时仍需另外实现、校准和验证派兵工具。

当前讨伐 skill 可由原生目录发现并按需加载；现有 DSH 工具仍缺少战斗、撤退、补兵动作与实时兵力字段，因此它不是独立自动战斗服务。有可用且获准的游戏 UI 控制能力时，可遵照技能执行并核验；否则报告缺失能力。

## 工具职责

| 阶段 | 工具 | 注意事项 |
| --- | --- | --- |
| 确认角色 | `sgz_game_status` | 取得 world 与校准状态 |
| 检索区域 | `sgz_search_map` | 检查 coverage、采集时间和 stale |
| 补充观察 | `sgz_scan_region` | 最多 2500 点，任务可暂停/恢复 |
| 查询编制 | `sgz_query_roster` | 主城/分城、所属城池、当前坐标和目的地分别返回 |
| 刷新编制 | `sgz_refresh_roster` | 必须先完成本机只读页面校准 |
| 战前评估 | `sgz_assess_combat` | 任意编制；只计算传入数据；战中返回等待，不检测兵力 |
| 多队打野 | `sgz_hunt_start` / `sgz_hunt_status` | 只有明确启动且实机能力完整才创建持续任务 |
| 暂停/继续 | `sgz_hunt_stop` / `sgz_hunt_resume` | 暂停不撤军，恢复先对账 |
| 使用技能 | `sgz_skills_list` → `sgz_skill_load` | 发现与加载不触发游戏输入 |

地图结果只覆盖观察过的点，不是全服地图数据库。城池队伍完整性依赖画面中读出的总数；没有总数或存在未读行时 `complete=false`。当前位置缺失时保持 null，不能拿城池位置或行军目的地替代。

## 本机校准接口

坐标导航继续使用游戏自身的地图入口。贼寇详情在 `ui.navigation.target_detail_crops` 配置额外 OCR 裁剪区域，在 `target_detail_region` 配置归一化标题/兵力区域。仅在该区域内以高置信度识别唯一标题，防止把其他地块或说明文本当作目标。未识别到标题时保持 unknown，不能当作确认没有贼寇。

编制读取配置位于 `ui.roster.pages`，每个页面包含：

- `actions`：已校准的只读动作名称；新增白名单包括 open_city_list、city_list_back、open_army_list、army_list_back、select_main_city、select_sub_city。动作的截图、点击点和验证区域放在既有 `ui.navigation.actions`。
- `optional_actions`：只有截图匹配才执行的动作，可用于切换页签、收起已展开卡片、滚到列表顶部；`after_optional_actions` 用于随后必需的动作。滚动同样要求目标与上下文图像匹配。
- `crops`：OCR 裁剪框，格式与现有 target_crops 一致。
- `anchors`：页面特征文字及归一化区域。
- `identity_checks`：本页可见、必须唯一一致的角色文字及归一化区域。
- `cities`：每行包含 id、kind（main/sub）、name_region、coordinate_region；可选 name_regions/name_separator 分段识别带分隔符名称，team_count_region 读取实际队数/容量。
- `armies`：每行包含 city_id、slot、name_region、current_region、destination_region、status_region；kind 区分 regular 与 auxiliary，佣工不视为战斗队。
- `total_cities_region`、`total_armies_region`：全角色城池/队伍总数所在区域；缺失时不声称全量覆盖。

不能凭空填坐标完成校准，需本机截图证据。当前缺少这些校准时，刷新会返回 `calibration_required:roster`，不会点击猜测的按钮。已存在的旧快照会继续明确标注时间和最近刷新错误。

本机已校准主城、当前城砦形态的分城、四支主城队伍和佣工列表。行归属根据本次城市内部编制核对配置，队伍名称作为页面校验锚点；改编、增队或调整布局后需要重新校准，不能直接沿用旧行归属。未验证全角色总数，当前快照保留 `complete=false`；这不影响返回已核验行的实际位置。

## 验证

`agent/scripts/Test-Dsh.cjs` 在独立临时技能目录中验证原生发现、加载、禁用、正文变更与新增文件热更新。测试不把 fixture 安装成用户技能，也不调用模型 API。`agent/scripts/Test-Agent.ps1` 还覆盖 Python 数据链路、世界隔离、断点恢复和 Node worker 生命周期。
