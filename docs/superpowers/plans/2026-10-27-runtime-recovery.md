# Runtime Recovery Implementation Plan

> **For agentic workers:** Use systematic-debugging, test-driven-development and verification-before-completion. Independent offline investigations may run in parallel; only the primary agent accesses the game.

**Goal:** Repair the replenishment recovery deadlock and diagnose the concrete missing hunt execution paths, retaining accurate live capability reporting.

**Architecture:** Keep the current Python/SQLite/native bridge. Separate evidence for read-only replenishment from evidence for executing 902. Preserve unknown historical commands and request replay results; permit only a new compatible absolute-target extension after two fresh matching observations. Keep unavailable hunt actions disabled until the corresponding implementation and live witnesses exist.

**Tech Stack:** Python 3.11, SQLite, Frida 17.24.3, TypeScript/Node DSH plugin.

**Spec:** Existing API replenishment plan and multi-army hunt skill; user's request to repair the failures found on 2026-10-27.

## Constraints

- Work in the deployed checkout, preserving the extensive pre-existing uncommitted implementation. A copy of affected source, tests, scripts and docs is saved under `.state/repair-backup-20261027-084208`.
- Never replay an unknown command or rewrite its historical result as applied.
- Read-only scope cannot grant execution. Identity, client version, implementation and incident binding remain required.
- No native game connections while a production worker holds the data lease. No concurrent diagnostic attaches.
- Native cleanup failure revokes read and write validation. Unknown command outcome revokes writing and retains read-only reconciliation if cleanup succeeded.
- Do not replace missing game semantics with constants or flip global hunt validation flags.

## Review Focus

- An old two-hero absolute target and a new three-hero target: old two goals must remain identical and satisfied.
- Reused request IDs, crash recovery and multiple pending commands must not cause resends.
- Non-idle, stale, wrong-identity, changed-capacity and duplicate-hero observations remain blocked.
- Installed smoke tests distinguish a working disabled path from verified gameplay execution.
- Battle opponent IDs must not be confused with map target entity IDs.

## Tasks

- [x] Add and run failing tests for separate read/write validation and strict read evidence; implement in `replenish_client.py` and the verification script.
- [x] Add and run failing tests for explicit replenishment reconciliation and compatible new targets; implement audited SQLite recovery in `replenish.py`.
- [x] Expose reconciliation through CLI and DSH, and show separate preview/execution readiness.
- [x] Correct the installed smoke test's unconditional API-ready assumption, preserving the actual live scan test when available.
- [x] Inspect missing hunt semantics using bounded metadata/status-only reads; fix proven mapping/logic defects without enabling unimplemented actions.
- [x] Run focused tests, full Python/Node suites and native syntax checks; perform a bounded live reconciliation/refill if its guards pass.
- [x] Deploy the resulting plugin, restart the single managed worker and record exact live outcomes and remaining unavailable capabilities.

## Verified outcome

375 Python and 12 Node tests passed. Installed 0.7.1 smoke checks passed. One controlled compatible refill of army 350212 returned both acknowledgements and all three heroes at 7000, with clean native teardown. Replenishment read/write validation is enabled; the original unknown command remains intact. Managed worker restarted as PID 22532. Full hunt execution remains incomplete and disabled: this plan repairs proven defects and records the remaining protocol implementation gaps; it does not claim all-army hunting works.
