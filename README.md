# 三国志·战略版 DSH Agent

这是 Windows 客户端自动化原型，使用 DeepSeek Harness 工具插件、Python 执行器和本地 PaddleOCR。当前已验证截图、扫码页识别、持久化任务和 DSH 插件通信；**扫码登录全流程与大地图探索尚未完成实机校准，不能作为可挂机版本使用。**

## 当前可用范围

- 按可执行文件路径和标题绑定唯一游戏窗口，截取可见客户区。
- 检查前台、遮挡、窗口位置及大小，拒绝超过 2 秒的输入依据。
- 识别扫码页、二维码过期、加载状态；未知页面暂停。
- 登录后台任务等待手机扫码，最多等待 5 分钟；角色选择和身份校验需要继续校准。
- 任务去重、暂停、重启后保持暂停，SQLite 保存记录及 JSON/CSV 导出。
- DSH 的 8 个工具已在本机 0.2.1-rc.3 的独立配置中加载和验证。

地图任务的蛇形顺序、最多 25 点、坐标复核和存储逻辑已有测试。实际导航适配器尚未实现；调用探索工具会暂停并报告 `identity_configuration_required` 或 `calibration_required:coordinate_navigation`，不会生成虚假探索记录。`calibrated=true` 本身不能使地图功能可用。

## 本机启动

项目位于 `D:\agent`，游戏为 `E:\sgzzlb\S3Client.exe`。Python 和 OCR 模型已安装在项目本地目录。

```powershell
cd D:\agent
powershell -ExecutionPolicy Bypass -File scripts\Start-Agent.ps1
powershell -ExecutionPolicy Bypass -File scripts\Test-Agent.ps1 -DshExecutable 'D:\deepseek\DeepSeek Harness.exe'
```

第一条脚本检查窗口和停止快捷键；第二条运行单元测试、构建插件，并通过安装的 DSH 运行时验证工具注册及只读状态调用。独立测试配置写入 `.state/dsh-smoke`，不会加载用户凭据或修改 DSH 的桌面配置。

需要手动截图时：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\Start-Agent.ps1 -Mode capture -Activate
```

截图保存至 `.state/capture.png`；扫码页面可能包含临时登录二维码，请用完删除。后台服务的 `observe_map` 不归档认证页面，只归档已识别为地图或详情的画面。

## 重新安装依赖

使用 Windows x64、Python 3.11 和 Node 22 或更新版本。已锁定依赖：

```powershell
uv sync --frozen --extra dev --extra ocr
Copy-Item config\game.example.json config\game.local.json  # 仅首次创建
cd plugin
npm ci
npm run build
```

本机也可使用 `.tools\uv\uv.exe`。首次识别会下载 PP-OCRv5 mobile 检测及识别模型，存于 `.state/models`；之后本地推理。不要同时安装多个 OpenCV 发行包，OCR 依赖已包含所需版本。

## 配置与校准

`config/game.local.json` 被 Git 忽略。关键配置：

- `executable`：客户端的绝对路径。
- `window_title_pattern`：默认 `^三国志`，排除该程序的无标题附属窗口。
- `identity`：待观察实际身份界面后配置区服、赛季、角色等核验值；空值不能通过登录成功判定。
- `ui.anchors`：基于真实截图的文字和区域特征。
- `ui.actions`：经实机确认的按钮归一化位置；`client_size` 必须与当前截图一致。
- `ui.calibrated`：当前为 `false`。缺少锚点、身份或导航实现时不可直接开启自动操作。

下一步需要：保持游戏在前台，手机扫码登录，进入大地图；核验区服/赛季/角色，观察坐标搜索与目标详情页面，然后实现并验证坐标导航。遇到角色选择会提示在电脑上完成选择。

## DSH 插件与工具

插件源代码在 `plugin/`，构建产物在 `plugin/dist/`，bundle 为 `plugin/cordis.patch.yml`。当前已验证隔离加载，**尚未安装到用户正在使用的 DSH 桌面配置**。后续接入需要将 `dsh-sgz-agent` 加入该 profile 的依赖和 bundles，保留已有配置。DSH 升级后需重新验证工具接口。

| 工具 | 用途 |
| --- | --- |
| `sgz_game_status` | 查看窗口、任务及校准状态 |
| `sgz_prepare_login` | 返回登录任务 ID，等待手机扫码 |
| `sgz_observe_map` | 读取当前画面；未知信息保持未知 |
| `sgz_inspect_target` | 单个坐标任务，目前因未校准暂停 |
| `sgz_explore_region` | 最多 25 点区域任务，目前因未校准暂停 |
| `sgz_pause_task` | 停止新输入并保存暂停状态 |
| `sgz_resume_task` | 显式恢复已暂停任务 |
| `sgz_export_results` | 导出已有记录，空任务不会虚构结果 |

同一操作重试须复用 `request_id`；改变参数须使用新的 ID。服务重启不会自动继续游戏操作。插件进程退出或超时不会重试可能产生副作用的调用，需重新加载插件后查看原任务并显式恢复。

底层协议为标准输入输出上的 JSON-RPC 2.0，每行一条消息。`Start-Agent.ps1 -Mode serve` 可供诊断使用；DSH 插件会自行管理服务，不要同时手动启动第二份服务。正常长任务立即返回 ID，后续用状态工具查询。

## 停止与故障

任务运行时按 **Ctrl+Alt+F12** 请求停止；拖动循环会释放自己按下的鼠标键。快捷键注册失败则不进入输入状态。焦点变化、窗口移动、遮挡、未知页面均会拒绝后续操作。

`window_occluded` 表示目标点被其他窗口遮挡；`not_foreground` 表示游戏不在前台；`observation_stale` 表示截图太旧；`data_directory_busy` 表示已有执行器使用同一数据目录。先排除原因，再显式恢复。Windows 调度仍存在检查与输入之间的极小竞态，强制杀进程也不能替代正常暂停，因此实机输入验证完成前请有人看管。

操作仅使用 Windows 截图和鼠标输入，不调用游戏私有接口，不读取游戏进程内存，不控制手机，也不保证不会被游戏检测为自动化。当前无派兵、战斗、交易或付费操作实现。

详细验证记录见 [P0 报告](docs/p0-report.md)，设计和计划位于 `docs/superpowers/`。
