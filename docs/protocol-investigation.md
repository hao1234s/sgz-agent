# 地图接口与快速扫描

2026-10-25：已实现并实测客户端接口扫描。它复用已登录游戏客户端的地图数据和现有网络连接，不需要前台、跳地图或 OCR；不是独立 HTTP 接口。

## 已验证的格式

| 接口 | 参数/结果 | 当前用途 |
|---|---|---|
| `logic.map.map_mgr.get_grid_res_by_pos(self,row,col)` | 返回资源配置 `id,name,level,land_type` | 批量读取资源地；UI x 对应 row，y 对应 col |
| `cs_get_shanzhai_data`，ID 925 | 无请求字段；响应 `infos: EntityInfo[]`、`bubbles` | 经现有 `send_get_shanzhai_data()` 请求个人蛮族列表 |
| `cs_get_zeibing_data`，ID 922 | 无请求字段；响应 `info: EntityInfo`、`state_info` | 经现有 `send_get_zeibing_data()` 请求木牛流马；已核对 zeibing_npc 配置 |
| `cs_center_rc`，ID 503 | `row:int,col:int` | 已核对消息定义，本版本不发送 |
| `cs_refresh_aoi`，ID 519 | `row:int,col:int,session_id:int` | 后续巨戟士任务已独立只读验证；0.5.0 通用扫描仍未集成 |

NPC 的 `EntityInfo` 包含 `id,type,monster`；`monster` 包含 `pos:{row,col},conf_id,army_conf_id,end_time,use_code,troop_type` 等字段。等级与显示名读取同 ID、同 conf_id 的客户端配置，不根据编号猜测。空的 zeibing `info` 表示该次个人贼兵查询无条目。

动态 AOI 的已知通知包括 `sc_notify_refresh_aoi_content`（720，`info_list,session_id,army_cnt`）和 `sc_notify_refresh_aoi_end`（721，`num,session_id`）。它们不属于当前扫描的完整覆盖承诺。

## 使用与范围

`sgz_scan_region` 默认 `source=api`，参数示例：

```json
{"request_id":"unique-scan-id","world":"使用 sgz_game_status 返回值","x0":567,"y0":804,"x1":616,"y1":853,"source":"api"}
```

返回后台任务 ID；通过 `sgz_game_status(job_id)` 等待 complete，再用 `sgz_search_map` 按 `kind`、`resource`、`min_level/max_level` 分页检索。矩形包含边界，坐标 1..1620，单次最多 2500 格；可按队伍位置构造矩形。50×50 是一次任务的上限示例，不是视野半径。新鲜扫描须使用新的 request_id。

- 资源是地图配置：有类型和等级，**占领者、可攻击性、守军当前状态未知**。缺失配置保持未知。
- 贼寇只覆盖服务器返回的当前角色个人蛮族/贼兵列表，并按矩形过滤。**不能据此宣称区域内所有世界怪物均已扫描**。
- 结果保留 `source=client_api`、`resource_source=client_map_config`、`bandit_scope=personal`、`occupancy=unknown` 和时间、证据路径。
- 不自动回落 UI；显式 `source=ui` 仍可使用原画面采集。

## 实测

当前客户端与角色，400 格 0.531 秒；2500 格 1.531 秒（适配器读取时间，不含插件启动与入库）。2500 格中识别 1257 个资源地、5 个个人蛮族。已交叉验证 `(592,830)` 四级铁矿、`(589,829)` 五级蛮族。

本机证据 `.state/network-probe/api-live.json`、`api-2500.json`；生产任务另存 `.state/evidence/api-*.json`。这些文件未纳入 Git。

## 运行与校验

依赖：`powershell -ExecutionPolicy Bypass -File backend/scripts/Sync-Backend.ps1`。适配器仅支持已核验的 S3Client.exe SHA-256；客户端升级需重新核验。`backend/config/game.local.json` 的 identity 除 role_id/region/world 外，还绑定运行时 `api_server_id` 和 `api_season`，切换赛季或区服不能沿用旧值。

`sgz_protocol_status` 检查依赖、配置和版本；登录与连接在实际扫描时检查。普通 Frida attach 后，在客户端已有 Lua 调用边界执行固定只读方法，每批 64 格，恢复 Lua 栈。不提供任意代码/函数入口，不读取连接密钥，不另建登录连接。扫描前后以及各批核验身份；等待两项服务器响应，超时、取消或断连不保存不完整结果。证据与坐标校验通过后，一次事务入库。结束卸载观察器、断开调试会话，保留游戏进程和原连接。

原先的 PCAPNG 离线诊断仍可使用，但不作为接口可用性证据。网络日志配置已恢复，PktMon 已停止。

后续巨戟士任务验证了普通地图贼寇的区域查询、配置分类与最近目标比较，详见 [分类与实测](bandit-classification.md)。`use_code=1` 是普通地图贼寇、2 是木牛流马、3 是个人蛮族，不能把三个接口分类混为一谈。查询结果新增 `npc_category/subtype/aliases/display_name`。
