# Map Search and Skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add evidence-based region searches, city/army snapshots, native DSH skill support, and start the installed agent.
**Architecture:** Extend the existing SQLite store and guarded Python executor; reuse DSH native skills. Separate cached queries from foreground acquisition.
**Tech Stack:** Python 3.11, SQLite, PaddleOCR, TypeScript, DSH 0.2.1-rc.3.
**Spec:** docs/superpowers/specs/2026-10-25-map-search-skills-design.md

## Global Constraints

- No combat skills or dispatch actions in this change.
- World isolation; latest observation precedes filtering; stale/unknown remain explicit.
- Existing 25-point tool unchanged; new scan limit 2500 points, 900-second execution rounds.
- UI acquisition requires calibration and verified identity; no guessed coordinates.
- Reuse installed native skill service; don't introduce a second skill interpreter.

## Review Focus

- A newer empty/unknown tile must hide an older matching resource or bandit.
- Unknown army positions and marching destinations must never become current coordinates.
- Incomplete roster refreshes must not masquerade as complete or preserve removed armies silently.
- Model-disabled skills must remain disabled through custom catalog/load tools.
- Worker startup/shutdown and data leases must not create duplicate control processes.

### Task 1: Map search and bounded scans
Files: src/sgz_agent/map_query.py, store.py, navigation.py, workflow.py, service.py; tests/test_map_query.py, test_navigation.py, test_service.py.
Interfaces: Store.search_map(world, x0, y0, x1, y1, **filters) -> results/coverage; region_points(..., max_points=25); dispatch search_map / scan_region.
- [x] Write tests for latest-only queries, world/region/category/level/freshness, paging, bandit recognition and bounds.
- [x] Run focused pytest and observe missing behavior.
- [x] Implement query validation, indexed latest observations, target parsing and resumable scans.
- [x] Run focused tests and the Python suite.

### Task 2: Own cities and army snapshots
Files: src/sgz_agent/roster.py, store.py, service.py, game_ui.py; tests/test_roster.py.
Interfaces: parse_roster(texts, profile) -> cities/armies/completeness; Store.save_roster(world, snapshot, evidence); Store.query_roster(world, **filters); dispatch refresh_roster / query_roster.
- [x] Write tests for city ownership, coordinate ambiguity, destination separation, unknown state, latest snapshot and staleness.
- [x] Run focused pytest to verify failures.
- [x] Implement structured snapshot and calibrated read-only page acquisition; expose actionable missing-calibration status.
- [x] Run focused tests and Python suite; live calibration if game is controllable.

### Task 3: Native skills, plugin tools, install and start
Files: plugin/src/index.ts, plugin/cordis.patch.yml, plugin/package.json; plugin/test/features.test.mjs; scripts/Test-Dsh.cjs, README.md, skills/.gitkeep.
Interfaces: DSH ctx.skills.snapshot/get with native filesystem provider; sgz_skills_list/sgz_skill_load; system prompt explains fresh queries and skills.
- [x] Write plugin contract tests and native DSH fixture integration for discovery/load/hot reload/disabled invocation.
- [x] Run tests to show missing behavior.
- [x] Add tools, native provider root and system prompt; eager startup with observable failure and teardown.
- [x] Build/package, run Python/Node and real DSH integration checks, install and launch the plugin-managed service.
- [x] Final independent code review and regression checks; document real limits and verification evidence.

### Follow-up: managed client
- [x] Restore/resize/activate the selected client and park its rendered cursor.
- [x] Verify stop handling, safe caption targeting and guarded scrolling.
- [x] Complete live city/army and bandit/resource reads, persist evidence, install 0.4.1 and restart the host-managed service.
