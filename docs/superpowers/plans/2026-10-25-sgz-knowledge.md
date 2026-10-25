# 三国志战略版知识库 Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement task by task. Steps use checkbox syntax.

**Goal:** 抓取首批武将、战法、基础机制并使 agent 可查询。
**Architecture:** 独立采集器、SQLite 实体存储、Qdrant local 向量索引、CLI/RPC 和 DSH 工具。
**Tech Stack:** Python 3.11、Qdrant client、FastEmbed BGE-small-zh-v1.5、TypeScript。
**Spec:** docs/superpowers/specs/2026-10-25-sgz-knowledge-design.md

## Global Constraints

- 仅公开官网内容；地区 cn；未知等级、赛季和日期不得推断。
- 不启动游戏或 OCR；不读凭据；数据在 .state/knowledge。
- 限速和缓存；成功、失败和覆盖范围明确记录。

## Review Focus

- 源站空响应或结构变化不能成为有效实体。
- 更新后不能检索到旧分段；索引故障可恢复。
- 本地向量存储并发访问不能损坏数据。
- 未知赛季不匹配指定赛季；引用与返回事实来自同一记录。
- 游戏不在运行时仍能执行知识查询。

### Task 1: 采集与规范化
Files: src/sgz_agent/knowledge_sources.py, tests/test_knowledge_sources.py, config/knowledge-mechanics.json
Interfaces: normalize_official(item, kind, table, fetched_at) -> dict; crawl(directory, refresh=False) -> report.
- [x] RED: 官网 fixture 应保留数值和来源、舍弃图片及列传、未知等级不伪装满级，缺少名称或描述拒绝入库。
- [x] GREEN: 实现公开内容接口分页、限速缓存、JSONL 导出、机制事实种子和失败清单。
- [x] 验证 pytest tests/test_knowledge_sources.py；真实抓取并核对数量。

### Task 2: 存储与混合检索
Files: src/sgz_agent/knowledge.py, tests/test_knowledge.py, pyproject.toml, uv.lock
Interfaces: KnowledgeBase(directory).ingest(records), index(), search(query, kind=None, season=None, limit=5), status(), close().
- [x] RED: 稳定 ID 去重、实体更新清除旧段、来源返回、精确名称优先、类别和赛季过滤、空查询拒绝、索引失败可重试。
- [x] GREEN: SQLite + Qdrant local；延迟加载本地模型；按字段切段；精确/词法/向量合并；锁保护同一存储访问。
- [x] 验证 pytest tests/test_knowledge.py；真实模型生成 512 维向量并执行中文查询。

### Task 3: CLI 和 agent 接入
Files: src/sgz_agent/knowledge_cli.py, plugin/src/index.ts, plugin/test/knowledge.test.mjs, README.md, docs/knowledge-report.md
Interfaces: python -m sgz_agent.knowledge_cli {crawl,index,search,status,serve}; JSON-RPC knowledge_search/knowledge_status.
- [x] RED: 独立 RPC 无需游戏配置，插件正确注册两项只读查询工具。
- [x] GREEN: 实现 CLI/RPC 与独立 worker，限定查询参数，更新版本、构建包和使用说明。
- [x] 验证完整 pytest、Node tests、TypeScript build、实际离线知识查询；记录来源局限和交付路径。

## Execution notes

在现有干净工作区内实施；基线 91 个 Python 测试通过。本地存储与模型选型为实现判断，按用户已确认的首批内容执行。

Final validation: 105 Python tests, 4 Node tests, TypeScript build, cached recrawl, idempotent indexing, offline query and installed DSH smoke passed. Independent review findings fixed; reviewer confirmed 13 targeted tests passed. Runtime data: 302 records / 343 vectors / 512 dimensions / 0 pending. No game input performed.
