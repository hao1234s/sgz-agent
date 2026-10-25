# 三国志·战略版 DSH Agent

Windows 客户端自动化原型，使用 DeepSeek Harness 工具插件、Python 执行器和本地 PaddleOCR。已实机跑通手机扫码后的身份核验、游戏内坐标查询、地块详情读取、坐标二次确认与导出。

手机扫码和授权由用户完成。已登录核验路径已实测；二维码失效后的自动刷新仍需校准。当前无派兵、战斗、侦察、交易或付费操作实现。

## 在 DSH 中使用

`dsh-sgz-agent@0.2.0` 已安装到本机 DSH desktop profile。重新打开 DSH 后可以输入：

> 查看游戏状态，读取配置的 world，然后探索坐标 (591,830) 到 (593,832)，完成后导出 JSON 和 CSV。只查看地图，不派兵。

先调用 `sgz_game_status` 取得 `world`，探索请求必须使用该值，避免跨区服、赛区或角色混写数据。游戏应保持前台，客户区大小保持 **1264×711**。运行中移动或缩放窗口会停止当次动作。

| 工具 | 用途 |
| --- | --- |
| `sgz_game_status` | 查看窗口、任务、校准状态和 world |
| `sgz_prepare_login` | 建立登录任务；已登录时核验角色编号、服务器和赛区 |
| `sgz_observe_map` | 读取当前地块坐标及可见详情 |
| `sgz_inspect_target` | 查看一个坐标，连续两次确认落点 |
| `sgz_explore_region` | 蛇形浏览矩形区域，最多 25 点 |
| `sgz_pause_task` | 停止新输入并保存断点 |
| `sgz_resume_task` | 显式恢复，重新核验身份并跳过已保存点 |
| `sgz_export_results` | 导出本地 JSON 或 CSV |

同一操作重试应复用 `request_id`，改变参数须使用新 ID。重启后任务保持暂停。插件退出或超时不会盲目重试；重新加载插件后查看原任务并显式恢复。

## 核验方式与停止

仅使用 Windows 截图、鼠标和数字键盘输入，沿游戏自带“地图 → 坐标输入框 → 前往”路径操作，不调用游戏私有接口、不读取游戏进程内存，也不控制手机。

任务先打开基础设置，读取并核验角色编号、服务器和赛区，然后返回地图。输入坐标后，两个输入框读回一致才点击“前往”；落点坐标连续两次一致才保存对应截图。看不清、矛盾或缺失的信息保持未知。

已校准动作比较按钮文字与页面上下文区域，避开飘雪和部队动画；未校准输入仍要求全画面一致。OCR 结果在点击前绑定到新截图。坐标标点不清时，可对同一图像做对比度处理后重新识别；不会把小数点直接替换为逗号，原始截图保留不变。

按 **Ctrl+Alt+F12** 请求停止。监听独立于 OCR，鼠标和修饰键会尝试释放；单个按键释放失败不阻止其他键释放，并保留失败键供关闭时重试。热键注册失败则不进入输入状态。

常见暂停原因：`focus_lost` 游戏失焦、`activation_denied` Windows 拒绝切换前台、`geometry_changed` 窗口移动或缩放、`screen_changed` 画面变化、`entry_coordinate_mismatch` 输入框读回不一致、`coordinate_mismatch` 落点不一致、`identity_mismatch` 身份不一致。先排除原因，再恢复。

这是限定客户端和布局的原型。新版本、分辨率、字体或皮肤需要重新校准；Windows 检查与输入之间仍有微小竞态，强制杀进程不能替代正常暂停。不保证游戏不会检测到自动化。

## 文件与验证

项目位于 `D:\agent`，游戏为 `E:\sgzzlb\S3Client.exe`。

- `config/game.local.json`：身份、世界标识及本机校准，不提交 Git。
- `.state/calibration/`：校准图；`.state/evidence/`：地图证据。
- `.state/agent.sqlite3`：任务与观察；`.state/exports/`：JSON/CSV 导出。
- `.state/package/dsh-sgz-agent-0.2.0.tgz`：插件安装包。
- `.state/dsh-profile-backup-20261025-101849/`：安装前包管理元数据备份，未读取或修改凭据。

```powershell
cd D:\agent
powershell -ExecutionPolicy Bypass -File scripts\Start-Agent.ps1
powershell -ExecutionPolicy Bypass -File scripts\Test-Agent.ps1 -DshExecutable 'D:\deepseek\DeepSeek Harness.exe' -Installed
```

第一条检查窗口及快捷键；第二条运行 Python/Node 测试、构建插件，并从用户安装目录加载插件进行独立检查，不调用模型 API。手动截图可用 `scripts\Start-Agent.ps1 -Mode capture -Activate`，输出 `.state/capture.png`。认证页截图请用完删除；后台不会把它归档为地图证据。

服务由 DSH 插件管理，不要同时手动启动另一份 `serve`。数据目录和游戏窗口均有互斥保护。

## 重新安装与开发

需要 Windows x64、Python 3.11、Node 22 或更新版本，依赖已锁定：

```powershell
uv sync --frozen --extra dev --extra ocr
cd plugin
npm ci
npm run build
npm pack --pack-destination ..\.state\package
```

本机 uv 在 `.tools\uv\uv.exe`。首次 OCR 会下载 PP-OCRv5 mobile 模型，之后本地推理。不要额外安装其他 OpenCV 发行包。

新机器可复制 `config/game.example.json`，但模板不含账号和图像校准，默认不会执行地图动作。安装插件前先完全退出 DSH，再运行 `scripts\Install-DshPlugin.ps1`；它调用 DSH 自带管理器并备份包管理元数据。

实机结果见 [验证报告](docs/p0-report.md)。
