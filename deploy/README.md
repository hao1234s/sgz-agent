# 多用户应用的 Linux 部署

此目录发布 NestJS 多用户服务、React 前端、官方 DSH headless、三国志插件/技能与 Python 基础包。服务不启动 DSH Web，不依赖桌面 profile，不开放 `/dsh`。浏览器通过自己的用户名和密码登录 `/api`。

服务器前提：Linux、Node.js 24+（`/usr/bin/node`）、npm、Python 3.11 及 venv/pip、Nginx、systemd，域名已有 Let's Encrypt 证书。SSH 账号需 root 或免密码 sudo，主机指纹须已在 known_hosts 登记。也可使用仓库根目录的 Dockerfile/compose.yaml。

## 准备和发布

本地先安装 `server`、`frontend` 的 npm 依赖。复制本目录 `.env.example` 到 `.env.local`，填写域名、SSH 用户和模型配置。SSH 凭据也可放根 `.env`；模型 Key 单独上传，不进入 release.tgz。

```sh
# 构建并生成本地发布包；不会连接服务器
.venv/bin/python deploy/deploy.py --domain agent.example.com
# 安装 SSH 上传依赖（只用于部署，不改后端锁文件）
uv pip install --python .venv/bin/python -r deploy/requirements.txt
# 显式部署到配置的服务器
.venv/bin/python deploy/deploy.py --apply --domain agent.example.com --user your-ssh-user
```

`--skip-build` 只打包已经构建并验证的前后端两个项目。发布包采用白名单，仅包含运行文件、锁定依赖清单、技能、Python 源码及部署模板，不包含 `.state`、账号数据库、本机配置或密钥。

安装器在 `/var/www/sgz-agent/releases/<ID>` 安装 npm 生产依赖与 Python venv，将 `current` 链接切到新版本。账号及原生会话数据固定在 `/var/lib/sgz-agent/data`，不会随 release 切换；服务配置与环境文件在 `/etc/sgz-agent/`。模型变量为 `SGZ_MODEL_API_KEY`、`SGZ_MODEL_BASE_URL`、`SGZ_MODEL_NAME`，保留已有 `DEEPSEEK_API_KEY` 环境文件的读取兼容。

## 首次创建管理员

在服务器上，用标准输入向 CLI 提供密码（至少 10 个字符）：

```sh
# root 的 Bash 终端
read -r -s -p 'Admin password: ' sgz_admin_password
printf '%s\n' "$sgz_admin_password" | sudo -u sgz-agent env SGZ_DATA_DIR=/var/lib/sgz-agent/data /usr/bin/node /var/www/sgz-agent/current/server/dist/admin.js create admin admin
unset sgz_admin_password
```

然后访问 `https://你的域名/` 登录，在账号管理中创建用户。CLI 管理账号不会重置活动任务状态。不存在默认管理员密码，也没有开放注册入口。

## 验证与恢复

`/healthz` 通过 Nginx 请求应用 `/health`，只证明服务在响应；还需登录检查账号隔离并发送一条消息，验证实际模型和工具。查看日志使用 `journalctl -u sgz-agent`；运行失败的诊断日志位于数据目录 `logs/`。

发布前备份 Nginx、service 和模型环境配置；健康检查失败会恢复旧配置与 `current` 链接。持久数据库不会自动回滚或删除。升级 schema 前需要单独制定数据回滚；本次 schema 为新表，并不导入旧 DSH Web profile 数据。

```sh
.venv/bin/python -m unittest discover -s deploy/tests -v
```

本地验证不会执行远程发布。Docker 镜像与实际远程服务器需在对应环境验证。

## 反向代理信任与数据权限

应用默认不信任代理头。`SGZ_TRUSTED_PROXY_CIDRS` 是逗号分隔的明确代理 IP/CIDR 白名单；仅在 socket 对端位于白名单内时，Express 才解析 `X-Forwarded-For`，登录限流使用校验后的 `req.ip`。不支持 `true`、跳数或全网 `/0`。本机 systemd/Nginx 模板仅信任 `127.0.0.1/32,::1/128`，Nginx 用 `$remote_addr` 覆盖转发地址，禁止使用客户端原值或 `$proxy_add_x_forwarded_for`。公网 Nginx 必须直接面对客户端；若增加 CDN/另一层代理，需独立配置并验证其真实地址信任规则。

Docker 默认同样不信任代理。在宿主机 Nginx 转发至 Docker 映射端口时，容器看到的对端通常是 Docker 网桥网关，而非 `127.0.0.1`。用 `docker network inspect <network>` 确认网关/子网，并将 `SGZ_TRUSTED_PROXY_CIDRS` 设置为实际 Nginx 对端的单个地址，例如 `172.18.0.1/32`；若代理是同网络容器，优先固定其地址并使用 `/32`。只有整个受控代理网络都可信时才配置该网络 CIDR，例如 `172.18.0.0/24`，且不要让其他不可信容器加入。不要复制示例网段或信任全部私网；必须保持后端仅能从受控代理访问。公开 HTTPS 部署同时设置 `SGZ_PUBLIC_ORIGIN=https://域名` 和 `SGZ_COOKIE_SECURE=true`。

服务与管理员 CLI 都将其 `SGZ_DATA_DIR` 收紧为 `0700`；进程使用 `0077` umask，SQLite 数据库和现有 WAL/SHM 必须由运行账号拥有并收紧为 `0600`，新数据库在打开 SQLite 前即以 `0600` 创建。Docker `/data` 初始为 `0700`，已有可写卷会在启动时收紧。部署时将已有数据及目录所有权交给服务账号，禁止符号链接数据库/侧文件。`SGZ_DB_PATH` 可单独指定共享目录中的数据库；应用只收紧数据库文件，不修改该自定义父目录现有权限。共享父目录本身应避免授予不可信账号写权限。
