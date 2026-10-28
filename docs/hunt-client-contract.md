# Hunting client adapter: verified scope and remaining gates

## 2026-10-27 verified corrections

The bounded read-only session `lifecycle-check-20261027-083715` completed roster,
four ready-army reads, owned bases and a server route preview, with zero native
callbacks/hooks left at teardown. The later `084831` session inspected fixed
morale/movement metadata and non-troop army status, also with clean teardown.
These observations do not validate gameplay execution or clear the incident.

Two adapter defects are corrected in 0.7.1. `enemy_army_id` is now retained under
its own name, not returned as a map monster `target_id`: no verified join to
`EntityInfo.id` exists. A direct preview's `need_time` is now the scheduler's
`attack_seconds` (the final attack **march**, not battle duration); deployment and
base preparation legs are structurally zero for direct routes. The raw server
`prepare_time` is retained separately as `command_prepare_seconds` and remains
unverified. Arrival morale and command preparation semantics still prevent a
verified route; no missing combat-duration value is required or invented.

All idle armies in the latest status observation had no retained command,
opponent, fight position or finish result. Idle therefore cannot resolve a prior
whole encounter after a restart. A persistent authoritative encounter witness
and validated target identity are still needed. Existing AOI samples suggest a
30-by-30 area and eight ID-based batches, but neither a complete 70-radius search
nor that inferred area has been proven. Current action methods and logistics
readiness are incomplete implementations, not merely disabled validation flags.

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

Latest recovery observation: after the separate logistics inspection failed and
all live interface work stopped, the user logged in again and reported that the
network warning had not recurred so far. OS-only checks found the new client
(PID 20728) responsive, the agent service running, and no remaining diagnostic
process. No new attach was performed. This is a user-reported recovery observation,
not a diagnosis or permission to clear quarantine; the causal link remains unknown.

The user then authorized a new bounded debugging/validation round, stopping on
any exception, and authorized ordinary replenishment plus all eligible armies'
hunting after validation. Two narrow diagnostics completed with zero gameplay
commands and verified cleanup (`lifecycle-check-20261025-225911` and
`lifecycle-check-20261025-230046`). Loaded logistics module fields and own barrack
state can be read without method invocation or bytecode extraction. The city's
generic `cur_troops` field was zero while its actual barrack records held troops;
the generic city value must not be used as the reserve count. Main-city barrack
indices 1/2/3 held 25000/23000/23000 and the subcity's index 1 held 2000 at that
snapshot; these are distinct pools with eligibility still unverified. Both army
conscription tables were empty. These observations do not enable command writes.

Both desktop control providers reported the native pipe unavailable. A passive
schema-whitelisted replenishment observer is prepared in
`backend/scripts/hunt_replenish_observer.js` to capture one ordinary user-operated refill
and its acknowledgement; it invokes no gameplay method. Its driver reads idle
army details beforehand and stops after an acknowledgement or a 110-second
observation limit. Syntax validation passed; the observer has not yet been run.

Desktop-control diagnosis was subsequently established without touching the game:
VS Code's Codex tool processes retain pipe `codex-computer-use-d28066a5-...`,
which no longer exists, while the current desktop host and `config.toml` use the
existing pipe `codex-computer-use-8920e46e-...`. Direct inspection of only the
relevant process environment variable confirmed the mismatch; JavaScript kernel
reset still inherits the stale address. The evidence is
`.state/diagnostics/desktop-control-connection.json`. Reloading the VS Code Codex
host was needed to reread its MCP configuration. After the user reloaded it,
window enumeration and game capture passed, verifying the pipe recovery. Window
activation and one title-bar-click retry both failed with
`failed to activate captured window`; the capture shows an elevated VS Code window
occluding the game. The next UI step needs the user to bring the game forward.
No game restart, permission change, direct helper launch, or gameplay command was
performed during this recovery.

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

### 2026-10-25 lifecycle follow-up

Both production read bridges now use `native_lifecycle.js` and
`native_session.py`. Terminal results are deferred until tracked Lua work has
returned and listeners have been detached. A per-invocation ticket prevents
duplicate/unpaired leave accounting; the closing gate ignores queued native
entries that arrive after shutdown, so they start no new Lua work.

One newly authorized read-only game session read the roster successfully:
four formed regular armies were idle in the main city, plus one empty slot and
two excluded auxiliary entries. It stopped before troop reads because terminal
state reported zero activity but the subsequent cleanup check did not pass.
Evidence: `.state/network-probe/lifecycle-check-*/001-roster.json` and session log.
The diagnostic retained its connection instead of forcing unload; the user then
closed the game normally and the diagnostic exited. No gameplay command was sent.

The discrepancy was subsequently reproduced with real Frida in an owned test
process: a queued late entry could change `active_native` from zero to one after
the terminal message. The closing gate fixes that reproduced race. This is
evidence for the new cleanup failure, not proof of the original attach crash's
root cause. Frida's documented per-invocation context, deferred callbacks and
listener removal APIs are described in the [JavaScript API](https://frida.re/docs/javascript-api/#interceptor).

Local inspection of Frida 17.24.3's Python `_rpc_request` also showed its condition
wait does not observe `Cancellable`; drain RPCs now use an independent bounded
wait in both production and diagnostics. A timeout retains the session and
persists the cleanup stage. Failed unload no longer forces detach.

Validation: 290 Python tests passed. The owned-process fixture keeps four native
call threads running while the real production cleanup drains, unloads and
detaches: 10/10 runs passed with the fixture alive. A never-resolving RPC returned
within its deadline while retaining the session. Evidence is
`.state/native-lifecycle-probe/concurrent-*.jsonl` and `stuck-rpc.jsonl`;
`backend/scripts/Probe-NativeLifecycle.py` is reproducible and never enumerates a game.
The final fixes subsequently passed one authorized game session at
`.state/network-probe/lifecycle-check-20261025-224159`: roster, four idle-army
detail reads, bases, and a TAOFA preview completed; each cleanup reported zero
tracked native work/callbacks/hooks and the session detached normally. Production
quarantine remains because full gameplay semantics are still unverified.

A separately authorized logistics-parameter inspection at
`.state/network-probe/lifecycle-check-20261025-224547` failed with
`access violation accessing 0x18`. Its cleanup and detach completed, but the
diagnostic did not identify the failing substage. Function-dump extraction has
been removed from the prepared follow-up; that change has not been run on the
game. The user later reported repeated network-quality warnings with uncertain
onset. No diagnostic process remained; the game was responsive and TCP connects
to its observed service ports succeeded (22/128 ms). Those facts do not exclude
damage to the client's Lua/session state from the inspection. Live interface
work stopped; the network warning's cause has not been established.

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
| DISPATCH | 19 | stationed only with verified arrival evidence below; otherwise relocating |
| ARMY_TENT_DISPATCH | 106 | relocating |
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
`MyArmy.dispatch_build_pos` identify current and dispatch positions. The legacy
`status.dispatch_pos` can still contain the home position after arrival. Current-base
identity is only asserted for a ready phase at the matching dispatch position.
`player_mgr.city.cities` yielded main city 144706 at `[591,828]` and subcity 263076
at `[552,1064]`. Other owned camp data comes from `player_mgr.build.builds`; only
the verified fortress/tent types 6 and 139 are currently considered, with
eligibility left false until capacity and recruitment support are checked.

On 2026-10-27, the user-selected role 113355381 had three parked armies whose
sole status was DISPATCH (19). A guarded ready read now requires a regular army,
no tying flag, matching valid current/deployment positions, a positive deployment
ID, a positive arrival timestamp no later than observation, and both movement
end/total times equal to zero. Missing, future, or active-movement evidence keeps
the army blocked. This establishes preparation readiness, never whole-encounter
completion. UI and bounded native observations are recorded in
`.state/network-probe/trial-preflight-20261027-095415/`. The resulting Python and
JavaScript guards passed the full 506-test suite.

The subsequent UI trial against giant 2511112 at (587,1550) sent a real
`cs_army_cmd` TAOFA (12), with auto-return enabled, but received boolean
`result:false`. Army 364067 remained parked at Fangling with unchanged troops
and stamina. This is a rejected submission, not a verified attack transport or
an encounter-completion witness. The request, response, and clean observer
shutdown are recorded in `.state/network-probe/runtime-check-20261027-100221/`.
Production execution remains quarantined pending positive end-to-end evidence.

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
27 tests; `node --check backend/src/sgz_agent/hunt_client.js` also passes. Full-suite counts
are reported by the integration run because other agents are still adding tests.

## Controlled Agent attack, 2026-10-27

The current role is `sgz:5841:PK26198:10028:113355381` (燕未还). The user
explicitly changed the arrival-morale floor to zero; the world-bound policy is
stored in `.state/hunt-user-policy.json`. The 20,000 troop floor remains.

`python -m sgz_agent.hunt_trial` is a bounded, explicitly authorized test entrypoint.
It refreshes the selected ordinary giant through a correlated AOI session, obtains
a server route preview, and reads ready-only troops, stamina, live decree supply,
the command's costs and automatic-return support. Without `--execute` it sends
no gameplay command. The fixed sender only supports the observed TAOFA request;
it exposes neither arbitrary protocol names nor Lua input.

`.state/hunt-attacks.sqlite3` claims the request before entering the native sender.
Both army and target remain reserved for submitted, accepted and unknown outcomes.
Restarting or repeating a request cannot resend it. The production hunt and
standalone replenishment paths respect this fence. Untagged boolean replies do
not confirm or reject this request: a delayed reply may belong to an earlier call.
Only a subsequent matching movement witness confirms departure. Such a witness
does not establish victory, whole-target completion or safe release of the fence.

At 10:50 local time, the Agent sent army `364070` once from camp `45562` at
`(1344,1141)` against giant entity `2511312` at `(1277,1151)`. Fresh inputs were
27,160 troops, 48 decrees, cost 1 decree and 15 stamina; preview 1,005 seconds,
path length 68 and estimated arrival morale 79.2. Three defenders were verified;
their individual troop counts remained unknown. The native observer saw a true
response but did not capture the outgoing codec event, so the initial receipt
correctly remained unknown. A later state-only read showed command 12, status 2,
the exact destination, automatic return enabled, and movement ending at 11:06:48.
No hero/troop inspection was used after departure.

Evidence: `.state/hunt-trials/agent-trial-364070-2511312-20261027/` and
`.state/network-probe/agent-followup-20261027-105146/raw-roster.json`.
This validates a controlled departure, not unattended hunting. Historical
quarantine, missing complete search coverage, whole-encounter reconciliation and
deployment/refill capability gates remain in place.

The same trial subsequently completed with three cached result headers
`9177664`, `9177665`, `9177666`: all were victories at 11:06:49, for the same
role and all three prepared hero IDs, position `(1277,1151)` and group `2383131`.
That group matches `monter_id` in the preflight's exact AOI entity `2511312`.
The live result enum confirmed `VICTORY=1`. These headers were inspected only
after state-only observation showed automatic return; no report RPC was sent.
The army returned to camp `45562`, position `(1344,1141)`, with status 19 and
zero movement times. The trial outcome is recorded in
`.state/hunt-trials/agent-trial-364070-2511312-20261027/outcome.json`.

`is_tying` remained true after return. The existing ready-detail guard therefore
refused a post-return snapshot before reading heroes/troops. Its meaning in this
state is unresolved; it has not been cleared or ignored to send another command.
The victory record deliberately retains the attack fence and marks readiness
unverified. This single successful encounter does not enable the unattended
coordinator. The other five armies' second radius-70 sample found no eligible
candidate; coverage remains unverified, so this is not an exhaustive absence claim.
