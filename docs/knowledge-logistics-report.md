# 调动与补兵知识包

核对日期：2026-10-25。新增 `backend/config/knowledge-logistics.json`，共11条：8条有来源的机制摘要、3条用户策略。资料核对日期不代表已在当前角色、服务器或剧本中实测。

## 内容与来源

| 内容 | 来源及发布日期 | 采用范围 |
| --- | --- | --- |
| 营帐调动保持士气、改变出发/回城点；回营恢复士气和伤兵；营帐征兵需要额外铜币 | [官方《战事建筑介绍》](https://sgz.ejoy.com/gonglue/detail-3992.html)，2019-08-07 | 历史官方机制，拆成调动和补兵两条；不把伤兵恢复当成满编 |
| 营帐补兵效率和功能限制；分城独立城建效果 | [官方社区玩家建筑攻略](https://community.lingxigames.com/m/s1/strategy_detail/4265)，2019-10-18 | 历史玩家资料，保留限制；不采用文章示例成本或耗时 |
| 营帐、同盟城池作为调动中转点 | [青衣浊酒的S1开荒攻略](https://sgz.ejoy.com/m/strategy/detail-4701)，2019-12-12 | 玩家原始投稿；不保证任意建筑都可调动或征兵 |
| 主分城自动征兵队列和出征时快捷分配预备兵 | [官方4月23日更新公告](https://sgz.ejoy.com/m/huodong/detail-15663.html)，2025-04-22发布、次日更新 | 区分“队列征满”和“队伍满编”；功能存在不代表当前角色资源充足 |
| 调动、集结速度更新 | [官方4月29日更新公告](https://sgz.ejoy.com/m/huodong/detail-16363.html)，2026-04-28发布、次日更新 | 说明旧版固定耗时公式不可直接沿用；实际路线读取当前预计时间 |
| 军阵调动名额和城外第二征兵队列条件 | [官方9月16日更新公告](https://sgz.ejoy.com/m/huodong/detail-16592.html)，2026-09-15发布、次日更新 | 保留新赛季、军阵等级和皇图霸业进度条件，不推广到全部营帐 |

另核对了[寒轩的阵地攻略](https://sgz.ejoy.com/gengduo/detail-4414.html)（2019-11-08，玩家原创）。文章提到分城科技成熟后可调动主力补兵，但没有足够信息证明当前版本下任意主城队伍可以在任意分城征兵，因此没有把该说法写成通用能力保证。具体调动和补给资格需读取当前队伍、建筑和操作入口。

上述网页均实际读取正文和日期；其中部分旧网址及2026年4月公告在网页检索器中超时，使用公开网页的普通HTTP读取完成核对，没有调用游戏账号接口。

每条记录保留 `source_url`、`source_title`、`published_at`、`checked_at`、`source_type`、`statement_type`、`confidence`、`limitations` 和 `version_status`。旧攻略为 `historical`，较新公告为 `dated_source`。置信度描述来源对摘要的支持程度，并非当前账号适用概率。无赛季、等级或版本实测资料时不自动补齐。

## 用户策略与游戏机制分开

三条用户策略使用 `statement_type=user_policy`、`source_type=user_instruction`、`version_status=user_policy`，来源为本地溯源标识 `user://conversation/2026-10-25/logistics-policy`。该标识不是网页链接，也不声称这些要求来自官方。

- 回营兵力低于配置阈值时先补兵；当前任务阈值20000，可调整。
- 回到可用主城或分城后，按该队当前带兵容量补满，再继续任务；达到20000不等于城内已满编。
- 通常要求预计接战时士气至少100；使用任务配置，不当作游戏士气上限。普通出征的最后一段仍需核对士气。

这些资料属于检索结果，不会自动变更狩猎配置、触发征兵、改变兵种或执行游戏动作。后续明确的用户配置优先。

## 持久化与维护命令

新增显式导入命令；不会在启动或爬取时隐式导入用户策略。先停止持有知识库锁的DSH/知识worker，再在仓库根目录执行：

```powershell
./.venv/Scripts/python.exe -m sgz_agent.knowledge_cli import --source backend/config/knowledge-logistics.json --directory .state/knowledge
./.venv/Scripts/python.exe -m sgz_agent.knowledge_cli search --directory .state/knowledge --kind mechanic --query "营帐调动与士气"
./.venv/Scripts/python.exe -m sgz_agent.knowledge_cli search --directory .state/knowledge --kind mechanic --query "回城满兵策略"
```

导入沿用 `KnowledgeBase` 的进程间文件锁及线程锁。先完整验证来源和类型，使用 `curated:logistics:*` 命名空间，防止覆盖官网目录；仅同一知识包拥有的ID可更新。记录先原子替换写入 `records.jsonl`，再写入 `knowledge.sqlite3`，最后建立向量索引。保留JSONL中的已有目录和SQLite中独有的资料；对已有目录的SQLite版本不作覆盖。如果此前爬取尚未执行索引，目录JSONL/SQLite的既有版本差异保持原状，可通过原有 `index` 命令完成目录维护。

重复导入返回 `changed: 0`、`indexed: 0`；内容修改仅更新本知识包相应记录。此命令追加/更新条目，不会因新版包省略某条记录就删除旧记录。索引失败时，JSONL和SQLite中的新增记录仍可保留并进行词法检索；修复模型/索引问题后重复导入即可恢复。进程若在JSONL落盘后中断，也可重复导入或运行 `index` 恢复。

主任务已在确认没有知识 worker 持锁后，实际导入 `.state/knowledge`：首次 `changed:11,indexed:11`，知识库合计 389 条、430 向量、待索引 0。重复导入返回 `changed:0,indexed:0`。关闭并重新打开 KnowledgeBase，使用真实模型分别查询这 11 条的名称，首条结果均为对应的 `curated:logistics:*` ID；这证明落库和重新打开后的检索，不依赖聊天上下文。

## 验证

针对知识库的测试覆盖重复导入、更新、重新打开后检索、来源/策略区分、非法来源拒绝、目录保留、SQLite独有记录、现有目录版本差异、ID归属冲突、后续爬取/索引保留、模型失败恢复、worker锁，以及CLI完整入口：

```powershell
./.venv/Scripts/python.exe -m pytest tests/test_knowledge_curated.py tests/test_knowledge.py tests/test_knowledge_cli.py tests/test_knowledge_sources.py -q
```

结果：27 passed。使用轻量嵌入测试替身及真实SQLite/Qdrant持久化，避免测试下载模型。新增功能先运行失败测试，再实现；另以边界测试复现并修复过长名称在写入后才报错的问题。

全项目测试结果记录在多队打野部署验证报告中；这里的 27 项仅代表知识库范围。
