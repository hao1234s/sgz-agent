# Agent Conversation Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement task-by-task.

**Goal:** 在根目录 frontend 提供可运行、可接 DSH 和 SSE 的流式会话前端。
**Architecture:** React UI → 独立 transport → Vite 本地代理 → 已有 Agent。浏览器保存会话，凭据仅内存。
**Tech Stack:** React, TypeScript, Vite, react-markdown, Vitest.
**Spec:** [design.md](design.md)

## Global Constraints

- 所有变更在 frontend，不改用户已有改动，不启动游戏 worker，不云端发布。
- DSH 使用原认证和会话协议；演示必须标注，异常不得伪装成功。
- 通用 SSE 通过 fetch POST，AbortController 取消，无自动重发有副作用的请求。

## Review Focus

- UTF-8 分块边界和 SSE CRLF 跨块 → stream.test.ts。
- 结束前断流、HTTP 错误和取消 → stream.test.ts。
- DSH 文本提交重复/多个工具步骤 → dsh.test.ts。
- 会话切换或删除与正在生成竞争 → 浏览器验证，生成时限制会话变更。
- 移动端导航、中文输入法 Enter、键盘可达性 → 浏览器验证。

## Tasks

- [x] 1. 流式传输：先写失败测试和接口桩，运行 npm test；实现 src/lib/stream.ts、dsh.ts 和通用类型；验证测试通过。
- [x] 2. 会话 UI：src/App.tsx、components/、styles.css；实现历史持久化、Markdown、复制、停止、设置与明确演示。用浏览器验证桌面/移动端。
- [x] 3. 接入与交付：vite.config.ts 代理、.env.example、README；npm test、npm run build，独立评审后修复实质问题。

## Execution ledger

- Ruling: 用户明确指定根目录创建 frontend，直接在当前 checkout 新目录实现，不改既有文件。
- Ruling: DSH 已运行且认证请求返回 401；不读取凭据文件、不绕过认证，以设置页连接 token 完成标准认证。

- Completed: 18 automated tests and production build passed; browser desktop/mobile, SSE and DSH WebSocket fixture verified. Independent review blockers fixed and rechecked. Real authenticated DSH model turn still requires the user's connection token.
