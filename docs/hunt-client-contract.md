# Hunting client adapter: verified scope and remaining gates

`sgz_agent.hunt_client.HuntClient(config, stop_event=None)` is the narrow adapter
used by the persistent scheduler. It shares `client_api._bridge_lock`, checks the
verified executable hash and bound account/world, and restores the Lua stack after
each native call. There is no public arbitrary Lua, memory, protocol-name, or
network-argument entry point. It never reads connection keys or opens another game
login connection.

## Runtime incident and current state

**Live instrumentation is quarantined. The full hunting workflow is not validated
or enabled.** On 2026-10-25 at 21:33:15 Asia/Shanghai, Windows Application Event
1000 recorded an access violation in `frida-agent.dll` at offset `0x1313a1` in
S3Client PID 30980. The Windows report ID is
`ca6ff16e-3779-45ed-809c-481cd87c9cfa`, exception `0xc0000005`.

The incident is persisted in the configured data directory as
`hunt-client-quarantine.json`. Every adapter read checks it before attempting an
attach. `execute()` returns `rejected`, `sent=false`; it does not inject or send a
gameplay command. `capabilities()` reports the incident and individual missing
validation gates. Its `available` value is computed from those gates and the
runtime state; removing an incident record alone cannot validate unsupported
commands.

Observed sequence:

1. The read-only TAOFA preview and production roster/one idle army/base reads
   completed at 21:32:24. Evidence: `.state/network-probe/hunt-preview.json` and
   `hunt-adapter-live.json`.
2. A separate bounded module-method inspection completed and detached at
   21:32:48: `hunt-route-methods.json`.
3. The next inspector was written at 21:33:14. Its Python observer failed inside
   `device.attach(30980)`, before `create_script()` or `script.load()` and before
   any `observer_ready` message. Windows recorded the crash at 21:33:15.

All completed observers use `finally` to unload and detach. No concurrent root
agent Frida session or gameplay mutation was running. The available evidence
locates the failure in the instrumentation module; it does **not** establish which
prior operation caused it. A lifecycle concern to investigate is that a completion
message can reach Python before a native interceptor has fully returned. No
unverified cleanup change is represented as a fix, and no additional attach was
attempted before the user-authorized controlled session below. No protection bypass or alternate injection was
attempted.

The user subsequently authorized **one** controlled read-only session. On the
freshly enumerated sole client PID 33176, that one attach succeeded; a no-hook
alive check, path/hash check, and status-only read succeeded. Results were
deferred until the intercepted native call returned. A TAOFA preview then failed
the diagnostic script's `preview_decode` validation, and the test immediately
stopped without another operation or attach. The shutdown log confirms
`hooks=0`, `active_native=0`, and `callbacks=0` before unload and detach both
returned. The client remained alive and responsive, with no new Windows crash
event in the checked interval. This does not establish that the earlier crash is
fixed. Production quarantine remains active.

Evidence is `.state/network-probe/controlled-stability/session.jsonl`,
`001-status.json`, and `002-preview.json`. The diagnostic required exactly one
decoder return value and read the top stack slot; existing successful observer
code instead supports bounded multiple returns and reads `saved-count+1`.
The production decoder now follows that Lua return-stack contract, with an
offline regression that executes the actual JavaScript index helper. The
diagnostic did not record the failed count/type, so this correction is not
claimed as a confirmed diagnosis or a live-validated fix. The one authorized
attach was used; no follow-up attach was performed.

## Adapter methods

| Method | Return and boundary |
|---|---|
| `capabilities()` | `available`, `missing`, `validation`, `quarantine`, `readonly`, `mutations_enabled`; this is capability evidence, not a game command |
| `roster_status()` | `{complete,world,armies,observed_at,source}` from the own-army dictionary; no hero troop, morale, or map-query access |
| `ready_army(id)` | Status plus `heroes[{id,is_main,slot,troops,capacity,stamina,wounded_until}]`, `morale`, `stamina`, `ready`; refuses active/unknown armies and repeats the guard in the same native Lua turn before details |
| `bases()` | Owned city/camp metadata; unknown recruitment, permission, capacity, or queue eligibility stays unverified and unavailable |
| `search_giants(center,radius=70)` | Circle-filtered `use_code=1,conf_id=10` candidates from the existing AOI cache; `complete=false`, with provenance. This is not a fresh server radius-70 sweep |
| `route_options(army,target,bases)` | Narrow read-only server preview diagnostics. A successful movement preview retains exact seconds and energy, but lacks verified arrival morale and whole-action timing, so `verified=false` |
| `execute(command)` | Rejects unsent gameplay requests while execution semantics/runtime safety are unverified; never returns invented acceptance |
| `reconcile(command,status)` | `unknown` without a verified durable effect witness; never resends |

The scheduler must exclude `kind != regular` and `formed=false` slots. An auxiliary
army can appear in the dictionary without a regular `ArmyInfo` structure; it is
not accessed through regular-army getters. IDs are returned as strings.

`ready=true` means the guarded detail snapshot is structurally usable. It does
not mean the army has enough troops, energy, resources, orders, or a safe matchup.
The scheduler still enforces its policy and requires independently verified
stamina/order and recruitment conditions. Raw stamina fields may require the
client's recovery calculation before an exact dispatch decision.

## Verified live mappings

The module `logic.player.army.my_army_mgr.army_id_tbl` contains own armies.
Allowlisted fields are `army_id`, `__army_type`, `city_id`, `dispatch_build_id`,
`is_tying`, hero IDs, and selected `status` fields. Regular `ARMY_TYPE.ARMY=1`;
`SERVANT_ARMY=11` and `COMRADE_LEGION=7` are excluded from regular hunting.

`S1.cn.res_pro.army_attr` runtime enum evidence establishes:

| Client status | Value | Conservative adapter phase |
|---|---:|---|
| FREE | 1 | idle |
| MARCH | 2 | marching |
| STAY | 3 | stationed |
| FALLBACK / GOBACK | 4 / 29 | returning |
| DISPATCH / ARMY_TENT_DISPATCH | 19 / 106 | relocating |
| FIGHT / FIGHT_READY | 24 / 26 | fighting |
| DRAW | 25 | draw |
| WIN / LOSE | 27 / 28 | unknown; never whole-encounter completion |

An `is_tying=true` flag conservatively blocks details even if the primary status
is STAY or FREE. Its behavior across a whole encounter still needs verification;
the adapter reports `unknown` with `tying_flag_semantics_unverified`, not an
assertion that the army is in a draw. At the first live observation, army 350213
had STAY plus that flag; the controlled session observed FREE plus the same
flag. It was not inspected for troops/morale. Army 350209 was an empty slot.
Armies 350210–350212 were idle.

`city_id` is the ownership/home-city identity. `dispatch_build_id` equal to that
home city is represented as no separate deployment base. `status.pos` and
`status.dispatch_pos` identify current and dispatch positions. Current-base
identity is only asserted for a ready phase at the matching dispatch position.
`player_mgr.city.cities` yielded main city 144706 at `[591,828]` and subcity 263076
at `[552,1064]`. Other owned camp data comes from `player_mgr.build.builds`; only
the verified fortress/tent types 6 and 139 are currently considered, with
eligibility left false until capacity and recruitment support are checked.

The ready read used the existing `get_cur_shiqi()` getter and the corresponding
own entries in `logic.hero.hero_mgr.hero_dict`. For idle army 350210, the live
snapshot contained troop counts `0,0,0`, capacities `9500,7000,9700`, morale 120
and raw stamina 120. This observation does not validate another army's readiness.

Do not invoke `cs_get_army_status` (980) as a fighting-army status-only poll: its
`MyArmy` response includes `ArmyInfo` morale fields. Likewise, a single
`sc_army_battle_result_notity` (1041) victory is not sufficient evidence that all
defenders of a target have finished. The adapter does not synthesize
`encounter_complete`, `completed_target_id`, or a whole-target result from idle,
WIN, or a missing target.

## Protocol preparation and preview evidence

The decoded `server_bin.schema.json` supplies genuine request/response shapes.
`prepare_protocol_command(command)` creates reviewable drafts and always returns
`sent=false`, `execution_verified=false`, and its missing semantic checks.
Drafts cannot be passed to a generic sender through this adapter.

| Operation | Verified schema | Outstanding semantic checks |
|---|---|---|
| Attack / mobilize | `cs_army_cmd` 501: `cmd,army_id,target_pos,...`; runtime TAOFA=12, DISPATCH=11 | Action defaults, exact target freshness, readiness, capacity, permissions, and acknowledgement behavior |
| Return | `cs_return_to_dispatch` 956: `army_id,target_type` | `target_type` enum and current legal destination; draft deliberately omits the unverified enum |
| Replenish | `cs_army_set_troops` 902: `army_id,hero_id_list,hero_troop_list,cons_index` | Own hero membership, correct conscription index, city reserve, queues, and actual resulting troop values |
| Camp recruit | `cs_fort_conscription_begin` 1753: `build_id,hero_id,target_troops` | Cost, capacity, own hero membership, queue eligibility, and authoritative result observation; no direct response exists |

Camp recruitment accepts **one hero per request**. Sending several requests as
one command journal entry risks an unrecorded partial result. The draft builder
requires one hero; the scheduler journals one deficient hero per command, waits
for recruitment/recovery confirmation, and retains the full-refill requirement
until all heroes reach their own capacity. This prevents partial-batch replay;
actual queue and result semantics remain unverified.

The successful read-only live request was:

```json
{"army_id":350210,"target_pos":{"row":579,"col":822},"cmd":12,"cmd_target_type":1}
```

`cs_army_preview` (502) returned `result=1`, `need_time=150`, `path_len=13`,
`speed=120`, `need_energy=15`, and `speed_up_factor=0.2`.
`PREVIEW_RESULT.SUCCESS=1` was independently read from the client enum. The
request is a route preview and did not dispatch an army. The new production
preview transport was added after the incident and has only offline syntax and
contract validation, not another live run.

The response did not include preparation time, whole encounter duration, or
arrival morale. No missing value is replaced with zero or a fabricated multiplier.
The schema permits `from_pos` for previews, but via-base route behavior and morale
still need actual client validation. An uncorrelated or concurrent preview is
rejected; the response schema lacks its own request ID.

## Next verification gates

1. Diagnose and fix the observed Frida attach/lifecycle failure before another
   controlled runtime test. Reopening the game does not clear the quarantine.
2. Verify whole-target completion identity and durable reconciliation witnesses.
3. Verify sequential AOI request completion and spatial coverage. Prior logs
   contain eight `sc_notify_refresh_aoi_end(num=8)` messages and different content
   chunks; neither an assumed shard count nor assumed radius is sufficient.
4. Verify actual route preparation/morale calculations and both legs of a
   deployment route, including legal base capacity and recruitment capabilities.
5. Verify action arguments against actual client call construction and command
   effects, then enable only the individually proven actions. No live attack,
   mobilization, return, recruitment, or replenishment was sent in this task.

The Python contract tests use explicitly synthetic data. They verify guarded
read access, status priority, missing completion evidence, command draft bounds,
quarantine behavior, missing preview values, and Lua multi-return stack indexing.
They do not represent live battle validation. The focused adapter suite passes
27 tests; `node --check src/sgz_agent/hunt_client.js` also passes. Full-suite counts
are reported by the integration run because other agents are still adding tests.
