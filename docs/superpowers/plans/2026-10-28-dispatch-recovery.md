# All-army dispatch recovery implementation plan

> **For agentic workers:** Use systematic-debugging, test-driven-development and verification-before-completion. Root implements the native adapter and integration; independent offline reconciliation and host-runtime investigations may run in parallel. Only root accesses the game.

**Goal:** Repair the reported blockers and let the existing DSH agent send every eligible existing regular army against a distinct verified giant in one dispatch round, without waiting for another army's battle.

**Architecture:** Retain the Python/native fixed-operation adapters and durable journals. Add narrowly validated status-read and single-round dispatch capabilities instead of advertising the still-incomplete continuous logistics coordinator as available. Keep original attack receipts and append fresh reconciliation evidence before removing an old army/target fence. Derive action readiness from current identity, executable, implementation digest and actual live evidence.

**Tech Stack:** Python 3.11, SQLite, fixed Frida/Lua adapters, TypeScript DSH plugin.

**Spec:** `../specs/2026-10-25-multi-army-hunt-design.md`, current local hunt skills, and the user's current explicit request to send all armies together and repair the diagnosed failures.

## Global constraints

- Preserve pre-existing uncommitted changes; no reset, commit, or worktree switch. Baseline is `.state/repair-baseline-20261028-082502` on `feat/sgz-agent`.
- The immediate authorized gameplay is one all-army dispatch round. Existing continuous hunt interfaces retain their broader readiness requirements; no unsupported continuous operation is advertised.
- Existing regular armies only; distinct live giant targets; no players, resources, other NPC types, formation changes, purchases or consumable acceleration.
- Use current identity-bound policy (minimum troops 20000, currently authorized minimum arrival morale 0). Check stamina, wounds, normal cost, auto-return and target identity per send.
- Never read details for marching, fighting, draw or ambiguous armies. No native diagnostic while the managed service owns the lease. Preserve unknown original receipts; no resend of a claimed request.
- Current false UI calibration is not flipped without visual evidence. A verified API status path can serve the actual API task independently.
- A nonfatal readiness rejection must not create a new native stability incident. Native faults, identity changes and unconfirmed cleanup still fail closed.

## Review focus

- Crash between native send and receipt persistence must not replay the command.
- Stale idle status, mixed identity, field-stationed armies and temporary battle states must not release fences or authorize reads incorrectly.
- One blocked army must not prevent other eligible armies from preparing in the same round.
- Single-round capability must not imply full radius coverage, final battle completion, relocation, recruitment or continuous hunting.
- Missing/stale native validation and DSH child-process failure must be reported exactly, without broad security configuration changes.

### Task 1: Repair read recovery and expected rejection handling

Files: `src/sgz_agent/hunt_runtime.py`, `hunt_runtime.js`, new narrow validation module and tests; service API integration.

- [x] Add a failing regression showing an expected army-readiness rejection is returned to its request, leaves runtime usable, and does not rewrite quarantine.
- [x] Implement explicit request-error handling for whitelisted semantic rejections; retain fatal behavior for all unrecognized/native failures.
- [x] Stop the idle managed worker, run bounded status-only native reads with current identity and clean teardown, save evidence and a scope-bound read validation record.
- [x] Expose the validated API status path; preserve UI calibration until separately verified.

### Task 2: Historical attack reconciliation

Files: new `src/sgz_agent/hunt_reconcile.py`, `tests/test_hunt_reconcile.py`; root integrates `hunt_attack.py`.

- [x] Test stale, wrong-identity, fighting/returning and incomplete-status rejection; original result remains immutable.
- [x] Implement append-only reconciliation witnesses and an explicit fence-resolution state based on fresh validated noncombat state, without claiming an unknown historical victory.
- [x] Take fresh controlled observations for each affected army and reconcile only evidence that passes the new checks.

### Task 3: Durable single-round dispatcher and verified native adapter

Files: new `src/sgz_agent/hunt_dispatch.py`, adapter module and tests; reuse validated trial preflight and attack guards.

- [x] Test all eligible armies are attempted in the same round, target uniqueness, independent blocked armies, stable selection, request idempotency, stale preflight and restart after an unknown send.
- [x] Implement a fixed-operation adapter with live candidate refresh, fresh ready-only preflight, server preview, normal-cost checks and matching own-army departure verification.
- [x] Require a dispatch-specific validation record; do not change the broader continuous-hunt flags.
- [x] Confirm all selected armies have distinct fresh targets and verified return destinations before sending; do not wait for another army's battle.

### Task 4: DSH tools and runtime repair

Files: `src/sgz_agent/service.py`, `plugin/src/index.ts`, associated tests; local host diagnostics if needed.

- [x] Expose single-round dispatch/status and narrow reconciliation to the agent with exact scope and reasons.
- [x] Investigate 0xC0000142 within DSH; apply only a demonstrated local fix, avoiding OS/app security changes.
- [x] Run Python suite, Node/plugin suite and native JavaScript syntax checks; perform independent review and fix material findings.
- [x] Deploy the plugin, restart one managed worker, hand off the authorized all-army dispatch, and verify actual departures and final batch receipt.

## Ledger

- Ruling: Continue in deployed feature checkout with full backup, matching the prior recovery plan. A new worktree would omit running local repairs and account configuration.
- Ruling: The user requested repair after an explicit diagnosis and dispatch instruction; this is execution authorization. Keep the existing architecture and proceed without another design approval cycle.
- Ruling: The immediate request is an all-army dispatch round. Its capability is separate from the unfinished indefinite logistics loop, so implementing it must not falsely enable that broader loop.
- Baseline: 533 Python tests passed in 46.09 seconds before new production changes.
- Read recovery: expected not-ready rejection now leaves the runtime usable; bounded live status reads closed cleanly.
- Reconciliation: five historical attacks retired with two independent fresh complete status witnesses. All original receipt JSON retained; new army/base mappings recorded in `.state/diagnostics/reconciliation-current-20261028.json`.
- Field ruling: the user authorized all existing armies. Current UI explicitly offers the field-stationed Guan Yu army a normal giant attack with `自动回城` checked. Allow that fixed game-managed return action, recording that the exact future endpoint is unverified; do not turn the reference deployment base into an endpoint claim or automatically reconcile field victory. Evidence: `.state/diagnostics/field-auto-return-ui-20261028.json`.
- Verification checkpoint: 656 Python tests and 13 plugin tests passed before final field and validation strengthening.

- Final software verification: 731 Python tests passed in 35.99 seconds; plugin 0.8.0 built and installed, 13 plugin tests passed.
- Live sampling: extended target refresh to 2 seconds and candidate dwell to 1.5 seconds after a real late response. Full 25-query field capture returned target 452681. Suspected cross-session deletion was not reproduced; no aggregation change made.
- First live round was deliberately stopped before writes for this diagnosis. All six records are verified unsent/blocked; request IDs and receipts retained. New explicitly authorized round uses `all-armies-20261028-repaired-2`.

- Round2: 347990,347991,347994 sent; matching own-army movement verified with strict claimtime/duration/target/auto-return join and receipts reconciled. Other3 had no send claim.
- Added per-chunk read retry only for exact foreign refresh after clean teardown; full candidate sampling is tentative until individual fresh recheck. Added same-runtime status-only departure fallback after missed passive packets, requiring clean closure.
- Latest full verification: 763 passed in 37.55s; independent review found no material replay/wrong-army/cleanup-bypass issue.
- Round3 selects only347987,347988,347989, with explicit known-unsent evidence and new bound live validation.

- Final state at09:10: four distinct armies/targets have strictly verified departure receipts (347990,347991,347994,347988); 347987 and347989 remain known-unsent with no fresh verified target within70. No unknown send remains. User asked to continue; radius preference question is pending (70 standby vs140 search).
- At-origin city marching status now reconciles only with full coherent identity/getter proof, preserving all timing/destination guards. Final Python suite:781 passed in35.73s. Managed worker restarted; expanding radius and refreshing the implementation-bound dispatch validation remain dependent on the user choice and fresh live preflight.

- User explicitly approved expansion and clarified that discovery radius differs from actual marching cells; verified zero-morale deployment legs must be excluded from morale consumption. The world-bound policy now authorizes a140 coordinate search circle, while each native query remains bounded to70. Candidate sampling does not claim exhaustive coverage.
- Current native route evidence plus UI43cells/preview44nodes established that `cs_army_preview.path_len` includes the origin. The live projection now carries validated path waypoints; preflight and final native guard independently derive actualcells=nodes-1 and bind the first/last waypoint to currentarmy/target. Forged count or route accounting fails closed. Arrival morale remains an estimate from current army morale/cost per cell.
- Read-only live static rule inspection confirmed `IGNORE_DEL_SHIQI_ARMY_CMD[11]=true`, with attack12 absent. Segment accounting retains all travel time and excludes only verified zero-morale relocation cells. However, current server previews ignore a hypothetical `from_pos`; they cannot verify a final attack from a future base, so no unproven relocation route was selected or sent.
- Expanded fresh search found347987→452680[695,1595] and347989→453862[454,1571]. Direct preflight gives actual76cells/760s/estimatedmorale74.4 and104cells/1040s/estimatedmorale57.6 respectively. Both satisfy the user's existing minimum arrival morale0. Evidence `.state/diagnostics/expanded-preflight-20261028.json`; implementation-bound validation regenerated from these clean live reads.
- Managed DSH host restarted and received `all-armies-20261028-expanded-1` for only347987,347989. Four previously verified departures remain reserved and excluded from resend. Await actual new departure receipts before completion.
- Independent review found that a forged1-node path between distinct endpoints could claim0 actualcells, and null accounting could downgrade exact-plan guards. Stopped expanded-1 before any send; both rows are blocked with `RuntimeError:stopped`, no target or command claim. Python and native JS now reject counts below the ordered-waypoint minimum possible moves, and exact native plans require full matching route accounting. The lower bound validates plausibility only; charged distance still comes from server nodes minus origin. Known76/104-cell native previews pass. Fresh live validation regenerated after the fix, and new request `all-armies-20261028-expanded-2` will carry only the same two known-unsent armies.
- Final independent review reproduced rejection of the original forged-zero and null-accounting cases; no additional material findings. Full Python suite840passed37.54s; changed native JavaScript syntax checks and git diff whitespace check pass. Expanded-2 prompt accepted by the restarted DSH agent after these checks.
- User steering: "换成打重徒兵吧" replaces the giant target with ordinary map重徒兵 (`use_code=1,conf_id=9`). Stopped expanded-2 before native send:347987 blocked verified_not_sent/stopped and347989 blocked/stopped. No new departure from either expanded batch.
- Current UI shows level9重徒兵 with33000x2, and a clean readonly native config read confirms `monster_npc[9].level=9,army_num=2`. Add an explicit world-bound target profile (9 only with dedicated user authorization; legacydefault10), enforce it in discovery, preparation, final native guard and durable batch replay. Retain actual-route accounting and140discovery policy.
- Parallel offline implementation continues with existing agents: dispatcher/policy/search profile; preflight/native final target guards; strict current-return reconciliation of four accepted old departures. Root alone controls native/UI and deployment. A returned-force reconciliation may permit a new target while preserving the old receipt/outcome unknown and retaining old target/spawn fences; never infer a giant victory from idle status.
- Retarget reconciliation completed for all four prior accepted departures with two fresh complete clean status witnesses and current owned-base evidence for the field-origin army. Original receipt bytes and old target/spawn reservations remain unchanged; only the corresponding army/formation claims released. Evidence `.state/diagnostics/retarget-reconciliation-20261028.json`.
- Native重徒兵 preflight verified entity451080[673,1617], ordinaryuse1/conf9, level9/two defenders, cost15stamina+1decree andauto-return. Direct path109actualcells/1090s; expectedarrivalmorale54.6. New plugin0.8.1 built/installed;13 Node tests pass. Full Python checkpoint898passed38.10s before the final refill status fix.
- Army347988 had18957/28600troops after returning. Its old refill adapter falsely rejected sticky `is_tying` despite unanimous current own-city idle getters. Replaced that blanket flag with a strict full status conjunction before any hero read.136 focused refill tests pass. Controlled normal reserve refill then actually applied:9643reserve used, all threeheroes at9500/9600/9500, both acknowledgements true and clean teardown. New commandf9ddb21865e34dae8f10bac656eb1668; no purchase or recruitment queue created. Evidence `.state/diagnostics/heavy-refill-result-20261028.json`; read/execute validation rebound from this actual result.
- Final verification after target9, retained-target reconciliation, near-target sampling, six-success confirmation budget and refill status guard:902 Python tests passed in40.00s;13 plugin tests passed; native syntax and diff whitespace checks passed. Independent review found no remaining material issue. Fresh9preflight validation regenerated and DSH restarted; accepted request `all-armies-heavy-20261028-1` selects all six current regular armies.
- Completed latest user objective: `all-armies-heavy-20261028-1` has6sent,0unknown,0blocked. Every army has an accepted durable attack receipt with independently matched current command12/marching status, intended distinct ordinary level9target, andauto-return=true. Actualroutecells for347987/347988/347989/347990/347991/347994 are32/18/27/18/14/34; expectedarrivalmorale100.8/109.2/103.8/109.2/111.6/99.6. This proves departures, not battle outcomes. Final artifact `.state/diagnostics/heavy-dispatch-result-20261028.json`. All source changes remain uncommitted in the existing user workspace; prior dirty work and original receipt history preserved.
