# 云端 DSH、前端与 Nginx

DSH 提供独立 CLI，可以在 Linux 服务器上使用 API Key，无需安装桌面客户端。本方案使用常驻 `web --no-open` 模式，为现有前端提供会话和流式接口；`headless` 适合一次性命令行任务。

```text
浏览器 → HTTPS Nginx
           ├─ /       → 本项目 frontend 静态页面
           └─ /dsh/   → 127.0.0.1:3080 的 DSH CLI → 模型 API
```

当前 Windows 游戏插件和 Python 执行器不装到 Linux 上。它们仍依赖运行游戏的 Windows 机器；云端调用这些工具还需另做经过认证的远程工具连接。本方案先部署可独立使用的 DSH 对话、内置工具与前端。

## 文件

- `deploy.py`：默认只在本机构建、打包；加 `--apply` 才通过 SSH/SFTP 部署。
- `install-cloud.sh`：服务器安装脚本，创建专用用户、固定版本 DSH、systemd 服务和站点配置。
- `nginx.conf.template`：HTTPS、SPA 路由、HTTP/WebSocket 代理和 cookie 路径处理。
- `sgz-agent.service.template`：后台服务，DSH 只监听服务器回环地址。
- `.env.example`：可提交的参数模板，复制为 `.env.local` 后填写实际值。

## 服务器前提

面向使用 systemd 的 Linux，建议先在 Ubuntu/Debian 上部署。需要 `/usr/bin/node` 为 Node.js 24+，并已安装 npm、Nginx 1.20+、curl、tar、util-linux、git；DSH 的终端/搜索工具还可能需要 bash、ripgrep 和可用的 Linux sandbox。部署脚本不会修改系统防火墙或卸载已有站点。

需要一个已解析到服务器的域名、开放的 443 端口，以及该域名的有效证书：

```text
/etc/letsencrypt/live/<域名>/fullchain.pem
/etc/letsencrypt/live/<域名>/privkey.pem
```

可用已有证书，或在上线前用 Certbot 完成签发。站点支持 webroot `/var/www/letsencrypt` 的续期挑战；首次签发与自动续期的安装由服务器环境决定。HTTP 80 用于 HTTPS 跳转和可选 ACME 验证。脚本假定 Nginx 已在运行且 `/etc/nginx/nginx.conf` 在 `http {}` 中包含 `/etc/nginx/conf.d/*.conf`。

SSH 登录用户需要 root 或免交互 sudo 权限。先用 OpenSSH 连接并核对服务器主机指纹，将正确记录保存到 `~/.ssh/known_hosts`；脚本不会自动信任未知服务器。

## 本机参数与凭据

从仓库根目录执行：

```powershell
Copy-Item agent/deploy/.env.example agent/deploy/.env.local
```

填写 `SITE_DOMAIN`、`SSH_USER` 和 `DEEPSEEK_API_KEY`。当前根目录 `.env` 的两行格式（第一行 IP，第二行密码）可以直接保留；脚本只读，不修改或打印它。也支持标准 `SSH_HOST=...`、`SSH_PASSWORD=...` 等 `KEY=VALUE` 格式。用户名不会从密码推测，必须明确填写或传 `--user`。

API Key 只在 `--apply` 时单独通过 SFTP 上传，服务器保存为 root 可读的 `/etc/sgz-agent/dsh.env`。`.env`、密码和 API Key 不进入发布压缩包。若服务器已有该文件，可留空本机 `DEEPSEEK_API_KEY` 以保留现有值。不同模型供应商也可以在 DSH 的 Models 页面配置；当前脚本预置 DeepSeek 官方 Key 环境变量。

## 准备与部署

```powershell
# 已安装前端依赖时，仅本机构建打包；不会连接服务器
.venv/Scripts/python.exe agent/deploy/deploy.py --domain 你的域名

# 真正部署才需要 SSH 客户端依赖；不改后端依赖锁
.tools/uv/uv.exe pip install --python .venv/Scripts/python.exe -r agent/deploy/requirements.txt
.venv/Scripts/python.exe agent/deploy/deploy.py --apply --domain 你的域名 --user 你的SSH用户名
```

首次构建前如缺前端依赖，先执行 `npm --prefix frontend ci`。默认读取根目录 `.env` 和 `agent/deploy/.env.local`，后者覆盖前者；可用 `--env`、`--config` 指定其他本地文件。`--skip-build` 仅用于打包已验证的 `frontend/dist`。

输出位于忽略提交的 `.state/deploy/release.tgz`。安装器使用专用 `/opt/sgz-agent/dsh` 目录，固定 `@deepseek-ai/dsh@0.2.1-rc.3`；不依赖全局 npm 包或桌面 profile。首次安装会从 npm 下载运行依赖，服务器需能访问 npm 和模型 API。

## 登录与使用

模型 API Key 用于 **DSH → 模型服务**；DSH 启动 token 用于 **浏览器 → DSH**，两者用途不同。

1. 在服务器以管理员身份查看 `sudo journalctl -u sgz-agent -n 100 --no-pager`，取得 DSH 打印的 HTTPS 启动链接。日志含连接凭据，请只在自己的终端查看。
2. 打开 `https://你的域名/`，在连接设置选择 **DSH Agent**，填写工作目录 `/var/lib/sgz-agent/workspace`，粘贴该 DSH 链接或其中的 token。
3. DSH 原生界面位于 `https://你的域名/dsh/`；需要选择模型、配置其他供应商或批准工具调用时，在这里操作。
4. 发一条普通测试消息，核实实际模型响应。`/healthz` 只证明 Nginx 站点已响应；安装脚本另外比对实际首页与发布文件。它们都不代表模型 Key、游戏工具或聊天已联调。

服务重启后以新的启动链接为准。不要把模型 Key 填入前端的 DSH token 输入框。前端的“自定义接口”模式目前没有云端 `/chat/stream` 服务，Nginx 对 `/agent-api/` 返回明确的 503；本部署使用 DSH 模式。

## 更新与恢复

重复执行 `--apply` 会创建新 release，保留旧 release 和 `/var/backups/sgz-agent/<发布ID>/`。切换前检查 `nginx -t`，服务或站点检查失败时恢复原配置、API Key 和静态入口。首次安装失败可能保留专用用户、运行依赖和未启用的 release，以便诊断；脚本不执行自动删除历史数据。

DSH 会话保存在 `/var/lib/sgz-agent/.dsh`，部署前端不会清空它。更新 DSH 服务会短暂中断会话连接，部署时应先完成正在执行的任务。连接中断或 SSH 超时后，先检查服务器服务与发布状态，再决定是否重试。

## 本地验证

```powershell
.venv/Scripts/python.exe -m unittest discover -s agent/deploy/tests -v
.venv/Scripts/python.exe agent/deploy/deploy.py --domain agent.example.com
```

生产服务器尚未执行安装；需要实际域名/证书、SSH 用户和模型 Key 后才能完成远程部署与模型联调。

实现依据：[DSH CLI](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/cli-help.md)、[官方反向代理说明](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/guide/public-deployments.md)、[API Key 配置](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/llm/llm-deepseek-api-key/README.md)、[Nginx WebSocket](https://nginx.org/en/docs/http/websocket.html)。

## 功能隔离边界

现有发布继续部署 DSH CLI、前端和 Nginx，不包含 Windows 游戏后端。仓库的 chat/knowledge/local profile 由 `agent/scripts/Render-Profile.mjs` 生成；本次隔离不修改服务器配置、安装插件或迁移数据。若后续需要给云端加入项目会话技能，应单独选择 chat profile 并验证资源路径；不直接复用 Windows local 配置。
