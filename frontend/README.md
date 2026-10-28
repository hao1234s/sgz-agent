# Agent 对话工作台

独立放在项目根目录 `frontend/` 的 React + TypeScript + Vite 前端。视觉参考 [OpenAI 中文官网](https://openai.com/zh-Hans-CN/)，提供浅色界面、会话侧栏、居中输入框和响应式布局。

## 启动

需要 Node.js **22.12+**。

```powershell
cd D:\agent\frontend
npm install
npm run dev
```

打开 **http://127.0.0.1:5173/**。依赖已经安装，后续直接 `npm run dev` 即可。

```powershell
npm test        # 流式解析与 DSH 协议回归测试
npm run build  # TypeScript 检查、生产构建
npm run preview
```

`npm run preview` 默认地址为 http://127.0.0.1:4173/。

## 连接现有 Agent

本仓库的 `backend/src/sgz_agent/rpc.py` 是 **工具 stdio RPC**，`agent/plugin/src/index.ts` 将工具注册到 **DSH**。自然语言会话由 DSH 执行，前端使用其现有的认证 HTTP API 和 WebSocket 接口，不直接把用户消息发给 Python 工具服务，也不启动第二个游戏 Worker。

1. 使用已加载本项目插件的 DSH 实例。
2. 在 `frontend/.env.local` 中设置该实例的 HTTP 地址。首次可复制 `.env.example`。
3. 重启 `npm run dev`。
4. 左下角打开 **连接设置 → DSH Agent**，填写工作目录 `D:\agent`，粘贴 DSH 浏览器启动链接或其中的 `token`，点击 **连接 Agent**。

```dotenv
DSH_BACKEND_URL=http://127.0.0.1:3000
AGENT_BACKEND_URL=http://127.0.0.1:8000
```

当前机器的 `.env.local` 已填写开发时检测到的 DSH 监听地址；**DSH 重启后端口可能变化**。如果 DSH 实例尚未提供浏览器启动链接，需要从 DSH 取得该实例的连接入口。另起默认 web profile 不代表它已经安装本项目插件。

连接 token 仅用来调用 DSH 自己的认证入口换取 cookie，不写入 localStorage 或 `.env`。前端不会读取 DSH 凭据文件、自动批准工具调用或更改模型配置。遇到需要批准的操作，回复区域会提示回到 DSH 处理。

原生接入使用：

- `session/create`：创建前端自己的会话；后续复用返回的 sessionId。
- `session/follow` 经 `/api/remote.mux`：订阅实时文本和工具状态。
- `session/prompt`：发送带唯一 requestId 的用户消息。
- `session/updateQueue` / `session/cancel`：移除本次排队消息或请求停止对应执行轮次。

断线不会自动重发任务。停止请求无法确认时明确显示错误；已执行的工具动作不会因停止生成而撤销。此页面的删除会话只删除本浏览器记录，不删除 DSH 的后台会话。

## 接入通用 SSE 后端

在连接设置选择 **自定义接口**。默认接口 `/agent-api/chat/stream` 会被 Vite 代理到 `AGENT_BACKEND_URL` 的 `/chat/stream`。可以填写其他相对路径或完整 HTTP(S) URL；完整 URL 需要后端支持 CORS。

请求使用 `POST`，正文为：

```json
{
  "messages": [{ "role": "user", "content": "你好" }],
  "stream": true,
  "session_id": "前端会话 UUID",
  "model": "可选，未填写时省略"
}
```

历史消息中只包含用户消息和已完整生成的助手消息。可选 Bearer token 仅保存在页面内存，刷新后需重新填写。

后端返回 `Content-Type: text/event-stream`。推荐事件协议：

```text
data: {"type":"status","text":"正在查询资料"}

data: {"type":"delta","text":"你好"}

data: {"type":"delta","text":"，我可以帮你。"}

data: {"type":"done"}

```

同时支持 Chat Completions 风格的 `choices[0].delta.content`、`choices[0].finish_reason` 和 `data: [DONE]`。这是**流式响应格式兼容**，如果直接接第三方 API，可能还需后端适配请求中的 `session_id`。

错误事件：

```text
event: error
data: {"message":"模型暂时不可用"}

```

结束前断流会保留部分回复并提示错误。SSE 停止按钮中断 HTTP 请求；后端需要监听客户端断开来取消自己的生成工作。

## 已有功能

- DSH 原生流、通用 SSE、明确标注的本地演示。
- Markdown、列表、表格、代码块、回复复制。
- 会话新建、切换、搜索、删除和浏览器本地持久化。
- 停止生成、异常提示、工具活动提示、回到最新消息。
- 桌面与移动端布局、键盘访问、中文输入法支持。
- Enter 发送，Shift + Enter 换行。

修改连接方式、接口、模型或工作目录后请新建会话。对话记录只保存于当前浏览器，不在设备间同步。模型输出中的原始 HTML 不执行，图片以说明文本显示。

## 目录

```text
frontend/
  src/
    App.tsx                  会话与界面状态
    components/              输入框、消息、连接设置
    lib/dsh.ts               DSH 会话和取消协议
    lib/stream.ts            POST SSE 解析与请求
    lib/demo.ts              明确标注的本地流式演示
    lib/storage.ts           本地会话与设置
    lib/*.test.ts            协议与边界测试
    styles.css               响应式样式
  vite.config.ts             本地 HTTP / WebSocket 代理
  .env.example               后端地址示例
  docs/                      设计、计划与验证记录
```

## 验证与边界

已验证 TypeScript 构建、SSE 中文分块与 CRLF、多行事件、错误、断流、取消，以及 DSH 请求格式、排队取消、轮次归属、失败结束和浏览器 WebSocket 传输。浏览器验证覆盖演示回复、停止、立即刷新保留、桌面和手机布局。

**真实 DSH 的认证后模型会话尚未联调**：当前运行实例未提供连接 token，未经认证返回 HTTP 401。没有把协议夹具或本地演示当作真实 Agent 回复。

本项目按本地工作台运行。`dist/` 是静态文件，正式部署时需要额外配置 `/dsh` 的 HTTP/WebSocket 代理和 `/agent-api` 代理；Vite 的开发代理不会包含在静态产物中。
