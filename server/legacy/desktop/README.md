# 旧桌面接入材料归档

这些文件保留迁移前的 Windows/DSH 桌面安装、启动和人工验收记录，**不是当前应用的可执行入口**。脚本内旧路径、独立插件包名和对应说明保留为历史原件；不应按这些旧命令启动新的多用户网站。

当前唯一入口与安装步骤见根 [README](../../../../README.md)，Python 能力见 [执行器说明](../../workers/python/README.md)。原说明在 `README.original.md`，原插件描述在 `plugin-package.original.json`。profiles 仅由 `server/scripts/render-profile.mjs` 用于离线能力装配验证，服务端生产配置由 `src/agent/runtime.ts` 显式生成。

当前服务端不安装桌面 profile、不启动游戏客户端，也不使用此目录的凭证/运行配置。未来需要独立客户端接入时，应依据用户/设备绑定方案重新实现安装器；不要恢复旧的顶层 agent/backend 目录。
