# Hunt execution continuation

Spec: ../specs/2026-10-25-multi-army-hunt-design.md

The user requested implementation after runtime recovery. Preserve the deployed
dirty checkout; baseline is `.state/hunt-implementation-baseline-20261027`.
Root owns native/UI testing and scheduler integration. Independent search and
replenishment recovery work follows the existing plan's parallel-agent instruction.

## Tasks

- [ ] Capture fixed, whitelisted army-command and status notifications during a bounded controlled client action. Verify exact giant identity, command fields, final encounter semantics and return destination.
- [ ] Implement durable command evidence and lifecycle reconciliation, retaining receipts across restart and never replaying an uncertain write.
- [ ] Implement actual validated command transport and client readiness. Keep capabilities bound to identity, client version and current implementation evidence.
- [ ] Implement correlated spatial search accumulation, reject stale targets, and validate coverage before claiming exhaustive radius-70 search.
- [ ] Make a confirmed compatible replenishment successor retire the old conflict fence without rewriting historical unknown results; integrate refill ownership with hunt journal.
- [ ] Run focused regression, whole suites, independent review, package deployment and bounded live acceptance. Restart the single managed worker.

## Rulings and progress

- Ruling: Work in the existing feature checkout with a full baseline backup; switching worktrees would omit the running, uncommitted repair and local identity configuration. No commit or reset is part of this task.
- Ruling: Prior single-session diagnostic approvals in the October 25 plan are historical. The current request continues the user's test-and-repair authorization. Controlled tests are bounded; implementation alone does not start an indefinite campaign.
- Pre-flight: Native code shares the refill helper prefix and its evidence digest. Keep new hunt modules separate where practical, and invalidate/revalidate any capability whose implementation changes.
- Pre-flight: Hunt and standalone refill journals both fence mutations. Integration must link ownership rather than skip either journal's unresolved commands.
- Pre-flight: An AOI response does not describe its bounding box. Repeated end messages or observed entity extents are insufficient proof of exhaustive search.
