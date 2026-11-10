# 三国志 Agent 多用户工作台

网页通过账号登录统一的服务端。服务端管理用户、权限和聊天记录，启动官方 **DSH headless 0.2.1-rc.3**，加载自己的插件与技能，按需调用 Python 计算执行器。

```text
frontend/                    React 网页
server/                      唯一后端应用与 Node 构建入口
  src/auth/                  登录、账号管理、权限与凭证
  src/conversations/         会话、消息、SSE、取消及运行生命周期
  src/agent/                 DSH 调度、工具插件、原生会话持久化
  src/storage/               SQLite 和数据文件权限
  skills/                    三国志技能与参考资料
  workers/python/            Python 计算、知识库与客户端能力包
  legacy/desktop/            旧桌面启动材料归档，不参与服务启动
  test/                      服务端与插件测试
  scripts/                   构建及离线验证脚本
deploy/                      整套应用的 Linux 发布、Nginx、systemd
```

浏览器只调用 `server` 的 `/api`。`agent` 是服务端内部能力，Python 是它调用的执行器；没有第二个 Python 网站服务，也无需安装 DSH 桌面插件。模块职责见 [服务端说明](server/README.md)。

每个账号只能操作自己的会话；管理员可以创建、停用账号和重置密码。账号停用或重置密码会撤销登录并停止该账号的活动任务。聊天记录和 DSH 原生会话保存在服务器，可刷新、换浏览器登录及重启续聊。

## 本地启动

需要 Node **22.19+**、Python **3.11**、npm 和 uv。从项目根目录执行：

```sh
npm --prefix server ci
npm --prefix frontend ci
UV_PROJECT_ENVIRONMENT="$PWD/.venv" uv sync --project server/workers/python --frozen
# 已存在的模型配置不要覆盖
[ -f server/.env ] || cp server/.env.example server/.env
npm --prefix server run build
npm --prefix frontend run build
npm --prefix server start
```

打开 **http://127.0.0.1:3080/**。`server/.env` 配置 `SGZ_MODEL_API_KEY`、`SGZ_MODEL_BASE_URL` 和 `SGZ_MODEL_NAME`。DeepSeek V4.1 Flash 的 API 模型名为 `deepseek-flash`（[官方说明](https://api-docs.deepseek.com/en/)）。Key 仅由服务端读取。没有 Key 时，账号和历史仍可用，聊天明确显示模型未配置。

首次安装时创建管理员，已有账号不需要重新创建：

```sh
# Bash；输入时不显示密码，密码至少 10 个字符
read -r -s -p 'Admin password: ' sgz_admin_password
printf '%s\n' "$sgz_admin_password" | npm --prefix server run admin -- create admin admin
unset sgz_admin_password
```

开发时分别运行 `npm --prefix server run dev` 和 `npm --prefix frontend run dev`。Vite 默认在 http://127.0.0.1:5173/，通过 `/api` 代理至 3080；`frontend/.env.local` 的 `SGZ_BACKEND_URL` 可覆盖目标。修改服务端插件后重启 dev，它会先重新构建插件和运行时资源。

## 数据与部署

默认持久数据仍在根目录 `.state/server/`，模型配置仍是 `server/.env`，Python 环境仍在根 `.venv/`。目录整理不会重新创建账号或迁移数据库。配置 `SGZ_DATA_DIR` 时，应保持该目录及会话工作目录稳定，DSH 续聊会核对工作目录。备份使用 SQLite 一致性备份或先停止服务，避免遗漏 WAL。

支持 [Docker Compose](compose.yaml) 和 [Nginx + systemd 部署](deploy/README.md)：

```sh
docker compose --env-file server/.env up --build -d
docker compose exec -T agent node server/dist/admin.js create admin admin < /private/path/admin-password.txt
```

Compose 默认只绑定主机 loopback 3080。公开到 HTTPS 域名时，设置 `SGZ_PUBLIC_ORIGIN` 和 `SGZ_COOKIE_SECURE=true`；可信反向代理 IP/CIDR 通过 `SGZ_TRUSTED_PROXY_CIDRS` 显式配置，禁止无边界信任转发头，详见部署说明。数据保存在持久卷 `agent-data`。

当前支持单个服务实例、多个账号，使用 SQLite WAL。每账号同时最多一个任务，全局默认四个，由 `SGZ_MAX_RUNS` 调整。模型执行仅开放会话技能和纯计算；Windows 游戏操作、任意 shell、文件读写、网页和子代理工具未启用。历史能力的代码保留在 Python 和内部插件中，需要独立的用户绑定与执行端设计后才能接入多用户平台。

## 验证

```sh
# 自动构建服务端及插件，然后运行 HTTP/隔离/DSH/插件测试
npm --prefix server test
npm --prefix frontend test
.venv/bin/python -m unittest discover -s deploy/tests -v
# Python 离线基础测试需要 dev 依赖
UV_PROJECT_ENVIRONMENT="$PWD/.venv" uv sync --project server/workers/python --frozen --extra dev
.venv/bin/python -m pytest server/workers/python/tests/test_config.py server/workers/python/tests/test_analysis_cli.py server/workers/python/tests/test_combat_assessment.py server/workers/python/tests/test_rpc.py server/workers/python/tests/test_dependency_contract.py -q
```

DSH 自动化测试使用真实固定版本运行时和本地模型协议夹具；外部模型连通性需另行验收。Python 客户端集成测试可能依赖 Windows，不作为服务端启动条件。旧目录迁移对照见 [服务端结构](server/README.md)。
