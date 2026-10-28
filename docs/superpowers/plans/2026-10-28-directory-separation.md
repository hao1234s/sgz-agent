# Agent / Backend Directory Separation Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Put DSH integration and skills in `agent/`, Python backend implementation in `backend/`, and keep the repository root uncluttered.

**Architecture:** `agent/plugin` owns tool registration and prompts; `agent/skills` owns model workflows; `backend/src/sgz_agent` owns execution, persistence and client adapters. Preserve the Python package name and existing shared `.venv`, `.state`, and `.tools` directories. `rootDir` continues to mean the repository root.

**Tech Stack:** Python 3.11, setuptools/uv, TypeScript/Node, PowerShell, React/Vite.

**Spec:** User request in this session: separate agent features from backend logic and clean the root directory. Routine directory and path changes only; preserve current behavior and all uncommitted edits.

## Global Constraints

- Work in the existing dirty checkout; a clean worktree would omit the user's current work. Do not commit or reset unrelated edits.
- Keep `frontend/` and shared `docs/`; move plugin, skills and DSH lifecycle scripts to `agent/`.
- Move Python source, tests, configuration, lockfile, package metadata and client probes to `backend/`.
- Keep runtime data at the existing root `.state`; update relative config paths without altering identities or calibration.
- Do not restart the running game worker or run live gameplay probes. Build a replacement DSH package for the next controlled installation.

## Review Focus

- Plugin startup must resolve `backend/config/game.local.json`, and skill discovery must use `agent/skills`.
- Local config relocation must resolve to exactly the same data directory.
- The editable Python install must resolve the relocated package from any working directory.
- PowerShell/CJS launchers must derive the repository root from their new depth.
- Backend test fixtures, JS assets and knowledge source packs must remain resolvable.

### Task 1: Move files and repair entry points

- [x] Establish baseline: Python 1004 passing, plugin 13 passing, frontend 18 passing, both Node builds passing.
- [x] Add and run a plugin startup regression for backend config, skill directory and config overrides.
- [x] Move `plugin`, `skills`, DSH scripts into `agent`; move Python project and probes into `backend` using checked paths.
- [x] Update runtime, script, test and config references, preserving root state and environment.
- [x] Reinstall the local editable Python package without changing dependency versions.

### Task 2: Documentation and validation

- [x] Give root README a concise directory map and entry points; keep detailed agent/backend documentation within their folders.
- [x] Update current document links, examples and ignore rules.
- [x] Run Python and plugin suites and builds, check frontend remains valid, parse PowerShell scripts, verify imports and data paths, and build the plugin archive.
- [x] Review the final relocation diff against the pre-move snapshot; record any live installation limitation.


## Results (2026-10-28)

- Backend: 1009 tests passed after the final config fallback fix.
- Plugin: 14 tests passed; TypeScript build and npm archive contents verified.
- Frontend: 18 tests and production build passed before relocation; frontend source remained byte-for-byte unchanged.
- PowerShell and CJS launchers passed syntax checks. The relocated Test-Agent.ps1 completed backend tests plus plugin build/tests from the repository root.
- Frozen uv sync dry-run checked 90 packages and would make no changes.
- Editable import and CLI help succeeded from an unrelated directory. Existing and example config paths resolve to root .state; omitted data_dir now has the same default.
- Snapshot audit retained all 201 original files; local identity/calibration and Python dependency metadata were preserved. Current documentation links resolved.
- Independent review identified the omitted-data_dir fallback; reproduced with a failing regression, fixed it, and reran the full backend suite. No other relocation issue was reported.
- Existing live DSH worker was not restarted or reinstalled. Updated archive is prepared at .state/package/dsh-sgz-agent-0.8.2.tgz; root README documents installation after the current task and DSH exit. Live integration is not claimed.
- Changes remain in the user's current working tree; no commit, reset, merge or push was performed.
