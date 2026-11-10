# 服务端目录收口

用户已确认：保留 frontend，Agent 作为 server 内部能力，Python 位于 server/workers/python，部署移到根 deploy。沿用当前工作区中已完成但未提交的多用户改造；不重新询问已确认方案。

## 目标与边界
- server 是唯一 Node 后端构建/启动单位。src/auth 管账号及认证；src/conversations 管会话、SSE与运行控制；src/agent 管 DSH 启动、插件、原生持久化；src/storage 管共享 SQLite 基础设施。
- agent/plugin/src 合并到 server/src/agent/plugin，使用 server 的依赖、编译和测试。agent/skills -> server/skills；backend -> server/workers/python；agent/deploy -> deploy。
- DSH 自定义原生持久化 provider 收入 server/src/agent/persistence，随服务构建输出到 dist。
- Windows 本地 DSH 安装/profile入口是历史维护能力，归入 server/legacy/desktop，文档标明不用于网页服务。不启用、不执行游戏客户端操作。
- server/.env、现有账号、会话数据库、会话工作目录、根 .venv 保持原位置和语义；固定数据目录避免 DSH resume 工作目录不匹配。
- HTTP/SSE/账号权限、固定 DSH 版本、工具权限与单实例限制不改变。不新增技能管理平台、不改模型。

## 方案选择
用户选择了将 Agent 与 Python 执行器收进同一个服务目录的方案。继续并列三个顶层目录会保留歧义；把全部 Python 改写成 TypeScript 成本过高且无业务收益，因此保留 Python 语言包作为 server 下的 worker。

## 验证
复用真实 HTTP 权限/取消/限流测试、真实 DSH 本地协议续聊与计算测试、插件能力测试；补可移动发布目录的真实 Python worker 和 DSH 产物路径验证。运行 Python 离线基础与模块边界测试、部署包内容测试。最后原数据重启当前服务并做真实模型 smoke；无需重新创建账号。
