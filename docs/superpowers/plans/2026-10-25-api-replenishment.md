# API Replenishment Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans inline, with test-driven-development and verification-before-completion. Review the native write boundary before deployment.

**Goal:** Expose `sgz_replenish` so the agent fills selected or all eligible armies using the existing authenticated game session, never UI input.

**Architecture:** A narrow Python action service journals requests and commands in SQLite under a cross-process lease. A native adapter snapshots idle own-city regular armies, reads ordinary reserve pool 1, rechecks the exact army and troops before sending only `cs_army_set_troops` 902, and observes acknowledgement plus resulting troops. A scoped validation record enables this action without enabling hunting or clearing the existing incident.

**Tech Stack:** Existing Python 3.11, SQLite, Frida lifecycle bridge, TypeScript DSH tool registration.

**Spec:** User's explicit request to encapsulate the successfully observed refill into an API-only tool; existing multi-army hunt design supplies battle, persistence and refill rules.

## Global Constraints

- No UI fallback, no arbitrary protocol/hero amounts, no gameplay command outside 902.
- Target values are full per-hero totals, not deltas. Verified ordinary reserve index is 1.
- No troop reads for fighting/draw/marching/unknown armies; no refill during tracked unresolved encounters.
- Initially support ordinary reserves in the army's own main/subcity. Imperial-guard pools and camp recruitment remain explicitly unsupported until verified.
- Unknown sends are never replayed, including with a new request ID. Duplicate request IDs return persisted results.
- Do not remove production hunting quarantine. A new native exception revokes the narrowly scoped action validation.

## Evidence

The Gan Ning UI operation sent army 350213, heroes [7254010942,5475010050,7255010022], target troops [9200,9600,9200], cons_index 1. Both the army-specific notification and request response were true; the UI changed from 18816/28000 to 28000/28000. The ordinary reserve changed from 25000 to 15816. This proves argument semantics, not every route or pool.

## Review Focus

- Current state changes between snapshot and send: recheck and do not send.
- A timeout/restart occurs after submission: persist unknown and fence new submissions.
- Different ordinary armies share one reserve pool: native-side fresh reserve check, serial commands.
- Another army is fighting: never enter its hero/troop reader.
- Cleanup fails or incident changes: revoke action permission; do not report success or enable unrelated actions.

## Tasks

- [x] Add failing tests for target totals, full/already-full, insufficient reserve, special pools, active armies, durable duplicate/unknown command handling.
- [x] Implement `replenish.py` action service and SQLite journal; share it between the DSH dispatch and a bounded verification CLI.
- [x] Implement `replenish_client.py` and `replenish_client.js`, reusing native lifecycle and read helpers; test serialization and transport boundaries.
- [x] Register `sgz_replenish` with world, request_id, optional army_ids and dry_run; update service routing and accurate capability text.
- [ ] Run targeted/full checks, a controlled API-only refill, then record the scoped validation and deploy the plugin update.
- [x] Report exact live results and remaining hunt/imperial/camp gaps; keep all unrelated validation gates closed.

Progress: 0.7.0 installed with execution validation closed. One API attempt returned unknown; its replay fence remains. Read-only reconciliation found the target no longer idle and did not enter troop reads. 319 Python / 11 Node checks passed. See `docs/api-replenishment.md`. Final native ack handling was revised after owned direct/indirect-hook fixtures proved reentrant observation omission; neither that fixture nor cached success grants live validation.
