# Agent 对话前端

用户要求在 D:/agent 根目录创建 frontend，与此目录对应的 Agent 流式对话，视觉参考 OpenAI 中文官网。用户指定就地创建，因此不另建 worktree，不改动已有 Python/插件工作区。

技术选择由实现者提出：React、TypeScript、Vite。DSH 原生 WebSocket 会话协议作为默认接入；通用 POST SSE 为可配置替代；演示模式由用户显式选择。全部新增文件放在 frontend。只本地运行，无云端发布。

核对结果：backend/src/sgz_agent/rpc.py 是工具 stdio RPC，agent/plugin/src/index.ts 把工具注册到 DSH。DSH 安装目录提供 session/create、session/prompt、session/cancel 和 /api/remote.mux 的 session/follow。不能把 Python 工具 RPC 当作大模型聊天接口，也不启动第二个游戏 worker。

界面：浅暖白背景、黑色正文、大标题、宽留白，左侧可折叠会话历史，居中输入框和四个开始入口。消息支持 Markdown、代码、复制；流式状态、停止和失败明确可见。对话历史只存在本浏览器。设置支持 DSH 连接认证或 SSE URL。凭据只留内存，不写 localStorage。

DSH 连接继续遵守其认证：用户提供启动 URL 中 token，经本地 Vite 代理走 DSH 自带根路径认证换取 cookie。未经认证显示需要连接。审批事件提示到 DSH 处理，不自动批准。SSE 支持 delta/done/error 与 Chat Completions content delta。

替代方案：只做 SSE 更简单但不能接现有 DSH；新建独立模型后端会重复运行环境和工具 worker。因此增加轻量原生协议适配，不更改原后端或模型配置。

验证：分割 UTF-8/SSE 帧、多行事件、错误、取消、DSH 原生帧及 RPC envelope；构建检查；浏览器桌面和移动端交互。真实 DSH 的模型回复需要可用认证，未能验证时明确报告。
