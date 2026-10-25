# Multi-army Hunt Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for the scheduler; independent client and knowledge domains use dispatching-parallel-agents. Steps use checkbox syntax.

**Goal:** Persistent multi-army giant hunting with 70-cell search, 120-second phase checks, deployment-aware refill and fastest feasible route.

**Architecture:** SQLite campaign/army/target/command journal; background coordinator calls a narrow client adapter serially. Skills route natural language to DSH tools. Curated logistics knowledge is imported into durable SQLite and the existing vector index.

**Tech Stack:** Python 3.11, SQLite, existing Frida native client bridge, TypeScript DSH tools.

**Spec:** ../specs/2026-10-25-multi-army-hunt-design.md

## Global Constraints

- radius=70, interval=120 seconds, policy threshold=20000, prepared morale=100.
- No troop/map queries for a team in active encounter; no actions for unknown states.
- No duplicate mutations after timeout/restart; no invented API semantics.
- No live campaign started by this development task.

## Review Focus

- Restart while command result is unknown: reconcile without resending.
- Two teams select same target: reservation permits one owner.
- Draw/intermediate defender victory: continue waiting regardless of visible troops.
- Deployment point changed/unavailable: verify actual legal return destination.
- City reserve shortfall or tent queue: wait until measured full/readiness, never claim replenishment from command acknowledgement.

## Tasks

- [x] Write scheduler/store tests using fake adapters for complete independent-team cycles and the review cases.
- [x] Implement `HuntStore`, `HuntCoordinator` and pure route/phase decisions in focused hunt modules; persist next polls and outbox before submission.
- [x] Inspect verified client APIs and implement `HuntClient` with truthful capability diagnostics and persisted runtime quarantine.
- [x] Implement curated logistics import and durable knowledge records; test reopens, sources and idempotence.
- [x] Connect AgentService lifecycle and start/status/stop/resume tools; never start from phrase in knowledge data.
- [x] Add multi-army skill and update single-army boundary; validate skill behavior, numeric gates and returned capabilities.
- [x] Run targeted/full tests, native DSH integration, import/index curated knowledge, deploy 0.6.0 and report actual live readiness.
- [ ] Finish real client execution/coverage/route/completion validation before enabling live hunting. The single authorized controlled read-only test is exhausted; production injection stays quarantined.

Validation: 285 Python tests, 10 Node tests, three skill format validators, and
installed DSH 0.6.0 native skill/tool RPC checks passed. The native run used an
isolated worker with a dummy executable and made zero game actions. Knowledge
production import and reopened real-model queries passed. Live hunting was not
started. See ../../multi-army-hunt-report.md and ../../hunt-client-contract.md.
