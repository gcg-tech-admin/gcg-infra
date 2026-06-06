# Nemesis Review — provenance-queue-master-plan-2026-06-06 (Round 1)

**VERDICT: CONDITIONAL**

## Patterns from review-log (applicable)
1. **Per-source resource governance** (knowledge-engine R1) — simple SELECT-where-due loop hides 4 failure modes
2. **Rollback scripts as phase gate conditions** (knowledge-engine R1) — write-first-rollback-never
3. **Plan-as-code gap** (search-sharpening-R2) — not directly code but the view+scanner combo is the artifact; plan must match what ships
4. **Claim-vs-ground-truth for "already exists" dependencies** (forge-cockpit) — `freshness_scan_log` is "unused" per plan's own data

---

## CRITICAL FINDINGS (block deployment):

**C1 — Freshness scanner (P2) hits unbounded concurrency on 971 entities with a single SELECT-where-due loop (root cause: no governor)**

The P2 scanner revives `freshness_scan_log` by running what looks to be a simple "find stale, enqueue re-runs" loop. However:
- **971 entities** (56+152+718+45) will reach `scored_stale` (day 1 = current sourced entities go stale; days 30-90 = all entities once loaded in burn-down)
- The scanner iterates over `sources` where `cadence_days` elapsed or `content_hash` changed. With 85 sources (expands as burn-down adds) the per-iteration work grows linearly with sourced count
- **No stated per-source timeout**: one hung source (e.g., a gazette PDF that 502s) blocks the entire nightly scan
- **No transient-failure recovery**: a 503 from an authority site flags the entity `stale` permanently unless the next scan catches it — but `last_fetched_at` won't update on failure so the entity stays stale forever
- **No cadence-drift protection**: if backlog from P3/P4 causes the scanner to run hours late across multiple days, cadence-based staleness drifts because entities that should be flagged at T+30d are flagged at T+30d+accumulated-delay

**Blast radius:** All 971 entities silently accumulate staleness drift. The burn-down board stays green because the scanner never gets to the right rows.

**Required change:** Add to Phase 2:
1. `max_sources_per_scan` config cap with spillover to next night
2. Per-source timeout (30s default) that skips and logs the source, does not block the scan
3. Transient-failure retry queue (retry at next scan, escalate after 3 consecutive failures)
4. Staleness baseline captured at scan start time, not computed from current time during iteration

---

**C2 — Legislation provenance is an extraction lift with no delivery evidence (root cause: 718 rows, 0% sourced, no extraction plan)**

The plan says legislation enters the view as `no_data` and P4 handles it. But:
- 718 legislation rows represents ~10× the currently sourced DTA count. There is no extraction plan, no source catalog for legislation (what gazettes/mof sites?), no per-law cadence assessment
- Law changes don't follow predictable cadences — they're event-driven (enacted, amended, repealed). A fixed `cadence_days` (even 365) is misleading because a law can change twice in a week
- The plan's own data shows **0 sourced, 0 scored** for legislation — this is a full greenfield extraction, not burn-down work. Phase 4's "mostly new run vs MoF/FTA/gazette" hand-waves the hardest domain

**Blast radius:** Phase 4 timeline is unestimable. Legislation is the largest table (718) and the most complex (event-driven freshness). If P4 stalls, the burn-down board shows 718 rows stuck at `no_data` or `unsourced` for weeks. Peter sees a board that is 40%+ red with no path to green.

**Required change:** Split legislation into its own sub-phase (P4a) with:
1. Explicit source catalog for UAE legislation (official gazette URLs, FTA rulings index, MoF decree portal)
2. `content_hash` as the *only* staleness trigger for legislation (no `cadence_days` clock)
3. A pilot extraction of 10 representative laws before P4 launches full-scale
4. Estimated effort (hours) and max entities per week, so the burn-down board shows a credible timeline

---

**C3 — No rollback procedure at any phase gate (root cause: view is mutable, scanner is destructive, but no undo path exists)**

- **Phase 1**: `provenance_status` view is created. If the view has a logic error (wrong state computation, misjoined entities), the board shows wrong data. Fix = ALTER VIEW or DROP+recreate. But there's no rollback script specified.
- **Phase 2**: Scanner flips rows from `verified_current` to `scored_stale` and enqueues re-runs. If the scanner fires with a bug (e.g., comparing `content_hash` against a wrong snapshot), entities are spuriously flagged stale and re-runs enqueued. There is no "undo stale flag" operation.
- **Phase 3+4**: Extraction and scoring produce data that enters the queue. No rollback = corrupted data that can only be fixed by re-extracting.

**Blast radius:** Any phase-gate bug becomes permanent data corruption. The board looks wrong; Peter loses trust in the provenace system.

**Required change:** Before each phase deploys:
1. Each phase creates a `provenance_queue_rollback_v<N>.sql` script that reverts its changes
2. Rollback scripts are verified (dry-run) against the actual database, not just saved
3. The scanner (P2) snapshots state before each run into a `scanner_run_snapshot` table so stale-flag can be undone run-by-run

---

## HIGH FINDINGS (must fix within 2 days):

**H1 — `freshness_scan_log` is claimed as "dormant" but unowned (root cause: Daen's Q4 unanswered in the plan)**

The plan's Red Line #4 says reuse `freshness_scan_log` but Daen's own Q4 asks *"Does the unused freshness_scan_log have an owning service already (even if dormant) before I assume it's free to extend?"* This question is unanswered in the plan. If `freshness_scan_log` is owned by a dormant cron job that gets re-enabled, or if its schema conflicts with the scanner's needs, P2 rework cascades.

**Required change:** Answer Q4 before P2 begins. If ownership is unknown, drop and recreate the table in a known migration.

---

**H2 — 51 unscored sources = 51 entities that can't reach `verified_current` (root cause: scoring is the final gate and it's under-target)**

P5 targets "score 51 unscored sources" — but these 51 sources serve the entities being loaded in P3/P4. Until they are scored, none of the entities they support can advance past `sourced_unscored`. The burn-down board surface-level green but the critical path is gated on a scoring batch that ships at the end.

**Blast radius:** After all entities are sourced (P4 complete), the board still shows most entities at `sourced_unscored` or `scored`. Green board is gated on source scoring that hasn't started.

**Required change:** Score sources in parallel with P3/P4 extraction, not as a sequential Phase 5. Each source, once used, gets scored immediately.

---

**H3 — Precheck guards against re-research but doesn't guard against `content_hash` false negatives (root cause: hash-comparison gap)**

The plan states: *"precheck guards all three: never re-research what's already verified_current and in-cadence"*. The freshness scanner uses `content_hash` comparison to detect source changes. But:
- If the source content changes but the hash stays the same (e.g., whitespace-only update, minor formatting), staleness is not detected
- If two different source documents produce the same hash (birthday collision at low hash width), a stale entity is incorrectly treated as current

**Required change:** Add `last_compared_at` to `freshness_scan_log` — hash comparison should also log the comparison timestamp. Implement a monthly full-hash-recompute for all `verified_current` entities to catch drift.

---

## MEDIUM FINDINGS (advisory):

**M1 — Entity union across 4 tables has no uniqueness guarantee. The view's `entity_type`+`entity_id` composite PK is assumed but not enforced, and the view definition is not specified. If two tables have overlapping entity IDs (e.g., a zone and a law with the same integer PK), the LEFT JOIN to `sources` produces wrong state counts.**

**M2 — Default cadence_days for DTAs (730) is approximately correct but the plan doesn't account for DTAs being updated (protocol amendments) mid-cadence. A treaty amendment doesn't reset the 2-year clock — it changes the content_hash. The plan's staleness model should treat DTA amendments as hash-change events, not cadence events, similar to legislation.**

**M3 — No alerting when stale count rises. The burn-down board shows state counts but P2 doesn't specify: what happens when the scanner detects 50+ stale entities in one night? Is there an alert? A dashboard? Or does Peter discover it at the weekly check-in?**

---

## NOTES

- Architecture is clean. The provenace state machine (6 states, simple transitions) is the right abstraction. Union view + scanner is the right architecture for a single-pane-of-glass board.
- The REUSE mandate is well-observed: existing tables, existing queues, no new pipelines. This is the right call.
- Daen's four open questions are the right questions. Answering Q4 (`freshness_scan_log` ownership) is a P2 blocker.
- Legislation (718 rows, 0 sourced) is the elephant — it dominates entity count, extraction complexity, and freshness semantics. The plan needs to acknowledge that legislation is 52% of the work, not a bullet point in P4.

**Severity summary:** 3 CRITICAL (concurrency in scanner, legislation underspec, no rollback), 3 HIGH (owned table, scoring sequence, hash false negatives), 3 MEDIUM. The architecture passes adversarial analysis; the execution plan at P2/P4 depth does not.
