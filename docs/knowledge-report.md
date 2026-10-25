# 三国志战略版知识库首批交付

日期：2026-10-25（北京时间）。已建立可供现有 DSH agent 查询的本地检索知识库，无需修改或训练聊天模型。

## 已入库内容

| 类别 | 条数 | 覆盖情况 |
| --- | ---: | --- |
| 武将 | 112 | 当前官网基础目录返回的五星武将 |
| 战法 | 174 | 官网基础目录返回 173 条 S 品质及 1 条 B 品质 |
| 基础机制 | 16 | 缴械、计穷、震慑、连击等简短事实摘要 |
| 合计 | 302 | 343 个向量片段，512 维，待索引 0 |

武将和战法由[官方资料库](https://sgzzlb.lingxigames.com/m/station/)页面实际使用的公开内容接口分页采集，无需账号。原始响应、请求参数、抓取时间保存在本地；单请求间隔至少 0.5 秒，失败有限重试。目录返回数量、发现数量、成功采集数量一致，错误列表为空。

基础机制引用[官方社区战法效果攻略](https://community.lingxigames.com/m/s1/strategy_detail/4292)，文章发布日期为 2019-10-23。种子文件只保存简短事实整理，保留来源及历史属性，未把文章的复杂叠加结论推广成当前规则。

**边界：这批不是全武将、全战法或全赛季完整库。** 未接入赛季增补表、A 级战法及社区配队攻略。官网数值通常未注明等级，页面也未给出内容更新时间；这些元数据保持 null，标记 `unverified`。抓取于 2026 年不代表数值在 2026 年仍有效；不会自动乘倍数推算满级。查询时应检查每条 `caution`，对最新赛季配队或精确战斗计算仍需补充相应版本依据。

## 存储和接口

- `D:\agent\.state\knowledge\records.jsonl`：可迁移的 302 条实体及来源。
- `D:\agent\.state\knowledge\knowledge.sqlite3`：结构化字段、文本和索引状态。
- `D:\agent\.state\knowledge\qdrant\`：Qdrant local 持久化向量库。
- `D:\agent\.state\knowledge\models\`：FastEmbed 使用的 BAAI/bge-small-zh-v1.5 本地模型。
- `D:\agent\.state\knowledge\raw\`：官网响应缓存；`crawl-report.json` 记录覆盖和错误。
- `D:\agent\.state\knowledge\query-check.json`：真实中文查询结果及引用。

`sgz_knowledge_search(query, kind?, season?, limit?)` 提供名称优先、词法与语义混合检索；`sgz_knowledge_status()` 查看覆盖范围、条数及索引。支持类别 `general / tactic / mechanic`，最多返回 10 条。赛季过滤使用已注明赛季的精确值，不将未知赛季当匹配。排序分数不是概率或置信度，语义召回还会包含相关但不直接回答问题的条目，agent 应阅读事实字段后作答。

独立 worker 不读取游戏配置、不加载 OCR、不操作游戏。进程间文件锁避免索引维护和 DSH worker 同时打开本地数据库；维护前退出 DSH。

## 验证

- 完整 Python 测试：105 passed；Node 测试：4 passed；TypeScript 编译通过。
- 真实模型生成 343 个向量，所有记录均已索引；重复导入和索引返回 `indexed: 0`。
- `曹操`：第一条精确命中曹操，返回兵种适性、战法及官方详情链接。
- `清除负面状态`（tactic）：第一条返回刮骨疗毒。
- `计穷和缴械有什么区别`（mechanic）：前两条包含计穷、缴械。
- `让普通攻击多打一次`（mechanic）：第一条返回连击。
- 设置 `HF_HUB_OFFLINE=1` 后仍成功查询，使用已下载模型。
- `dsh-sgz-agent@0.3.0` 已安装到本机 desktop profile；从已安装包启动 DSH 验证，10 项工具注册成功，知识状态及曹操查询成功。安装前元数据备份：`.state/dsh-profile-backup-20261025-114033/`。

重新打开 DSH，可直接要求：“查询曹操的兵种适性和自带战法，给出来源并注明数值等级是否已知。”

后续手动更新：先退出 DSH，运行 `python -m sgz_agent.knowledge_cli crawl --refresh`，再运行 `python -m sgz_agent.knowledge_cli index`。不加 `--refresh` 时复用缓存续跑。没有设置自动定时采集。

完整刷新目录后发现已消失的 ID 会标为历史归档，默认检索排除；刷新失败或目录不完整时不会误撤下旧资料。复现此边界的回归测试已覆盖正常撤下和网络失败两种情况。

独立代码审查发现并修复：撤下条目的归档处理、未完成索引的旧向量占满候选结果、源站空名称被字符串化。检索在向量排序前限定到可用的当前分段，旧版本和已撤下资料不会挤掉有效候选。
