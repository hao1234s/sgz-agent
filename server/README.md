# 统一服务端

此目录是应用唯一的后端构建、启动和发布单位。根目录执行 `npm --prefix server run build`，在同一个 `dist/` 下生成业务 API、DSH 插件及原生持久化 provider。插件没有独立的 npm 安装或构建入口。

| 模块 | 负责 | 不负责 |
|---|---|---|
| `src/app.ts` | 装配 Nest/HTTP、请求边界、静态网页 | 用户及会话 SQL 处理 |
| `src/auth/` | 登录、会话凭证、管理员账号操作 | 模型执行循环 |
| `src/conversations/` | 会话归属、消息、SSE、并发和取消 | DSH 插件实现 |
| `src/agent/runtime.ts` | 每轮 DSH headless 启动及清理 | 网站账号权限 |
| `src/agent/plugin/` | 工具声明、技能加载、Python 调用适配 | 第二套网站后端 |
| `src/agent/persistence/` | DSH 原生事件和会话恢复 | 浏览器提交的对话历史 |
| `src/storage/` | 共享 SQLite schema、查询和文件权限 | 模型工具权限 |
| `skills/` | 技能正文与参考资料 | 程序启动逻辑 |
| `workers/python/` | 计算、知识库、历史客户端执行能力 | 登录、网页 API |

数据流：网页请求 → 认证 → 会话服务 → DSH 运行时 → 工具插件 → Python worker。Python 通过 stdio JSON-RPC 按需启动，不监听第二个 HTTP 端口。不同用户的会话归属由业务 API 检查，原生事件桥仅对单次运行的指定会话开放。

`src/agent/contracts.ts` 定义业务会话层调用运行时的接口。认证层通过会话层提供的 `stopUser` 撤销活动运行；关闭服务也等待同一套运行清理，不另建取消逻辑。

## 路径与构建

- `src/agent/plugin/index.ts` → `dist/agent/plugin/index.js`。
- `src/agent/persistence/provider.mjs` → `dist/agent/persistence/provider.mjs`；TypeScript 构建复制该 ESM 资源。
- build 先清理旧 `dist`，防止已移动源文件残留为旧发布入口。
- 所有 Node 依赖由本目录 `package.json` / `package-lock.json` 管理。
- `npm test` 包含插件原有测试；`npm run test:plugin` 可只验证插件。
- `scripts/verify-profiles.mjs` 可使用明确指定的 DSH runtime 做离线能力装配验证，不访问真实模型或游戏。

## 旧目录对照

| 旧路径 | 当前路径 |
|---|---|
| `agent/plugin/src/` | `server/src/agent/plugin/` |
| `agent/plugin/test/` | `server/test/agent-plugin/` |
| `agent/skills/` | `server/skills/` |
| `backend/` | `server/workers/python/` |
| `agent/deploy/` | 根目录 `deploy/` |
| `agent/scripts/` 的桌面安装/启动材料 | `server/legacy/desktop/scripts/` |
| `server/src/runtime.ts` | `server/src/agent/runtime.ts` |
| `server/runtime/persistence.mjs` | `server/src/agent/persistence/provider.mjs` |

不保留旧顶层目录或兼容软链。原有 `.state`、`.venv`、`server/.env` 位置不变。已安装过 Python editable 包的开发环境需重新执行根 README 的 uv sync，使安装指针指向新 worker 目录。

桌面启动材料是历史参考，见 [归档说明](legacy/desktop/README.md)，不会进入服务端发布包。
