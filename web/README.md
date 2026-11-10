# Agent 对话工作台

React + TypeScript + Vite 前端，提供用户名/密码登录、服务器会话历史、流式回复和管理员账户管理。账户由管理员创建，没有自助注册。

需要 Node.js 22.19+。先按项目根目录说明启动 `server/`，再启动前端：

```sh
cd web
npm ci
cp .env.example .env.local # 已有本地配置时，只增加 SGZ_BACKEND_URL
npm run dev
```

访问 http://127.0.0.1:5173/。开发和预览服务器将 `/api` 代理至 `SGZ_BACKEND_URL`（默认 `http://127.0.0.1:3080`），保留浏览器 Host/Origin 以支持服务端同源校验。生产环境由应用服务器提供 `dist/` 和 `/api`；Vite 的代理不会进入构建产物。

```sh
npm test        # 认证、账户切换、流式传输、取消及历史协议回归
npm run build   # TypeScript 检查与生产构建
npm run preview # http://127.0.0.1:4173/
```

使用管理员提供的账户登录。管理员可在左下角“账户与管理”中创建、停用/启用账户和重置密码；重置密码、停用账户会撤销该账户的登录会话。普通成员只能使用自己的会话。首次管理员账户通过项目根目录说明中的服务端 CLI 创建。

模型参数与凭据统一在服务端配置。模型未就绪时，界面会提示联系管理员，发送按钮不可用。前端不提供 DSH token、任意接口、工作目录或本地演示入口。

会话列表、消息、删除和取消均调用认证后的 `/api`。浏览器只保留当前登录账户的内存状态；历史记录来自服务器。旧版浏览器 localStorage 对话不会被读取或导入。退出会立即清空界面并中止该账户的请求；再次登录可从服务器读取历史。停止生成会请求服务器取消本轮，再读取已保存的终止状态。断线不会自动重发消息。

保留原有浅色响应式布局、Markdown/表格/代码块、回复复制、会话搜索、回到最新消息，以及 Enter 发送、Shift + Enter 换行。原始 HTML 不执行，消息中的图片显示为文本说明。

主要文件：

- `src/App.tsx`：登录边界、服务器会话与流式状态。
- `src/components/Login.tsx`、`AccountPanel.tsx`：登录和管理员面板。
- `src/lib/api.ts`：同源 Cookie 请求、应用请求头和服务器 SSE 协议。
- `src/App.test.tsx`、`src/lib/api.test.ts`：账户隔离与传输回归。
- `vite.config.ts`：开发/预览 `/api` 代理。

旧 `dsh.ts`、`stream.ts`、`storage.ts`、`demo.ts` 和 `SettingsDialog.tsx` 仅作为历史实现保留，不被当前应用导入。对应旧协议测试不代表当前页面支持这些连接方式。
