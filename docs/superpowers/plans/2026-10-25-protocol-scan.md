# Protocol Scan Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement the verified tasks. Do not mark the protocol adapter complete without live evidence.

**Goal:** 将接口扫描作为明确目标，先消除扫描入口的静默 UI 行为并建立可靠协议诊断。

**Architecture:** 独立的协议状态、离线诊断和客户端只读适配器；服务和插件调用同一能力定义。已验证的资源 getter 与个人 NPC 服务器查询通过 Lua 调用边界执行。

**Tech Stack:** Python 3.11、标准库、pytest、TypeScript、Node test。

**Spec:** `docs/superpowers/specs/2026-10-25-protocol-scan-design.md`

## Constraints

- 既有修改保留；沿用当前功能分支，不提交无关用户工作。
- 协议未知即明确不可用，不回落为 UI。
- 原始抓包和本机诊断保存在忽略的 `.state/`；不得输出载荷。

## Review Focus

- source 遗漏或非法值不能触发窗口输入。
- 明确 UI 请求保持幂等与断点行为。
- 截断/损坏数据包不能被当成无网络流量。
- Wi-Fi LLC 格式及 TCP 重传不能导致错误字节统计。
- 诊断输出不得包含原始载荷。

## Task 1: 扫描来源与能力

- [x] 在 `tests/test_extended_service.py` 增加默认/显式 API 不创建任务、非法 source 拒绝和明确 UI 正常工作的测试，运行并见失败。
- [x] 在 `src/sgz_agent/transport.py` 定义 `protocol_status()`；修改服务入口和插件 schema、提示词。
- [x] Python/Node 测试通过。

## Task 2: 离线协议诊断

- [x] 在 `tests/test_transport.py` 编写 Ethernet、Wi-Fi、截断、重复段和载荷不泄露测试，运行并见失败。
- [x] 实现 `summarize_capture(path, local_ports)`，由 CLI `inspect-transport --input ... --local-port ...` 调用，不初始化游戏窗口。
- [x] 在真实捕获上执行，保存脱敏摘要与调查结论。

## Task 3: 真实客户端接口适配器

- [x] 从客户端已加载的协议定义核对消息 ID/字段；区分静态资源配置和个人动态贼寇。没有凭据提取或保护绕过。
- [x] 资源 getter 和 NPC 925/922 现有只读查询方法实测通过；四级铁矿 `(592,830)`、五级蛮族 `(589,829)` 与已有画面对照。
- [x] 实现每批 64 格的区域检索；400 格 0.531 秒、2500 格 1.531 秒，无 UI 输入。
- [x] 版本指纹、角色/运行时区服/赛季校验、完整响应验证、原子批量入库、来源与范围标注。
- [x] 独立审查后补充 lua_checkstack、阶段取消、Frida Cancellable 超时及独立停止热键。

**本阶段范围：** API 使用当前已登录客户端，不实现独立 HTTP/TCP 登录；地图资源是配置数据，个人 NPC 是服务器查询。全世界 AOI 怪物、占领/可攻击状态仍未知，不作为完成承诺。

## Ledger

- 2026-10-25：确认现有服务只有 UI/OCR。EStats 验证游戏 8443 连接有真实流量；改用本地端口过滤成功捕获。PktMon 的网卡包中存在 Wi-Fi LLC 帧，原 Ethernet-only 临时解析器漏报。暂不能由二进制数据认定加密方式或消息格式。
- 网络日志配置已备份，临时启用后客户端重启；需要用户手动扫码登录，问题已发出。没有执行身份验证自动化。
- 用户完成登录后身份和地图跳转复验成功，但未获取可读协议日志。原始日志配置已恢复。
- 审查发现同序号/长度不足以认定精确重复；先写两个失败测试，再加入内部 SHA-256 与捕获长度比较、完整载荷冲突计数，均通过。
- 完整验证：Python 160 passed，Node 8 passed；已安装 0.4.2，真实 DSH runtime 加载 17 工具并验证默认 API 扫描明确拒绝、已有查询和原生技能行为正常。安装备份 `.state/dsh-profile-backup-20261025-195731/`。
- agent 已重新启动，DSH 管理的 venv worker PID 3744；PktMon 已停止，过滤器已清理。第三项仍未完成，不应以诊断工具升级冒充接口快速扫描。
- 后续用户要求继续处理：完成 Task 3 的限定范围。0.5.0 已通过官方管理器安装，备份 `.state/dsh-profile-backup-20261025-201836/`。最终 Python 169 passed、Node 8 passed；安装后真实 DSH 400 格读取 0.407 秒，铁矿 4 级筛选通过，17 工具和 skills 五项行为验证通过。实机错误角色绑定被拒绝。正式服务已启动，venv worker PID 8668；游戏 PID 30980 保持运行。
- 独立审查的栈容量、生命周期取消与停止热键问题已修复。两项取消测试先失败后通过；最终使用可控时钟消除 10ms 测试时序依赖。日志 `.state/logs/api-installed-final.out`。
