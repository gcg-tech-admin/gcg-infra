# Wonhoo Review — provenance-queue-master-plan (Round 1)

**VERDICT: CONDITIONAL (HIGH)**

---

## PRACTICAL ISSUES

### C1 — Phase 4 (138 DTAs + 718 laws) has no time estimate, no throughput model, and no realistic path to completion

**The gap:** The plan says "mostly new run vs MoF/FTA/gazette" for 856 entities. Knowledge Engine R1's timeline (re-validated in KE R2) was Phase 2 DTAs: 4-6 weeks, Phase 3 legislation: 3-4 weeks. That's **7-10 weeks** of extraction/verification work on top of KE's existing phases. The provenance plan treats Phase 4 as a single line item with no duration, no throughput rate, and no acknowledgment that Varys is already capacity-constrained doing Knowledge Engine extraction.

**Why it matters:** If there's no model showing how 856 extractions fit into Varys's existing KE throughput (~2-3/session, 1 session/day), then Phase 4 has no timeline — and P3+P4+P5 together could exceed 3 months without anyone noticing until Week 8.

**Severity: HIGH**

**Required change:** Add a throughput model for Phase 4. Either: (a) state Phase 4 will reuse KE's extraction pipeline and add N weeks to KE's timeline, or (b) document that Phase 4 runs on a separate cadence (e.g. Varys does 5 DTAs/week, finishes in ~30 sessions). Pick one before Phase 2 starts.

---

### C2 — Freshness scanner ownership ambiguity

**The gap:** The plan revives `freshness_scan_log` in Phase 2 but doesn't answer Open Question #4 (does it have an owning service?). The schema exists but the question "is this free to extend or does it have a dormant owner?" is kicked to Daen.

**Why it matters:** If the table is used by another dormant process, extending it without coordination could cause schema conflicts, trigger unexpected behavior, or require permissions the scanner doesn't have. This is a trivial check (query `pg_stat_user_tables` + `information_schema.routines`) that should be resolved before Phase 2.

**Severity: HIGH**

**Required change:** Answer Open Question #4 as a Phase 0 task — check if `freshness_scan_log` has indexes, triggers, functions, or service bindings, and document the result in the plan before Phase 2 begins. Don't leave it as "open for Daen" — it's a 5-minute investigation.

---

### C3 — No timeline estimates at all across 5 phases

**The gap:** Zero duration estimates. P1 (view), P2 (scanner), P3 (pricing), P4 (tax/legal), P5 (scoring). The Knowledge Engine plan (which this reuses) had per-phase estimates. This plan has none. P4 alone could be 7-10 weeks — and Peter expects this as a "burn-down board that burns down."

**Why it matters:** Without timeline estimates, there's no way to tell Peter "this board will be green in N weeks." The burn-down board concept implies a time horizon. If it can't provide one, Peter won't know whether to invest resources now or defer.

**Severity: HIGH**

**Required change:** Add per-phase duration estimates:
- P1 (view) — should be 1-3 days (mostly wiring existing schema)
- P2 (scanner) — 2-5 days (extend existing table, cron wiring)
- P3 (pricing) — 1-3 weeks (depends on Varys throughput)
- P4 (tax/legal) — 7-10 weeks (856 entities)
- P5 (scoring) — 1-2 weeks (51 sources)

---

## COMPLEXITY FLAGS

### MEDIUM — Materialized view is premature optimization for 971 rows

The plan asks "view vs materialized view" for `provenance_status` UNION of 971 entities (56+152+718+45 = 971). A UNION of 4 small tables with LEFT JOIN to 85-row `sources` is trivially fast as a regular view. 971 rows doesn't need a materialized view — the potential cost saving on a 971-row query is nanoseconds. Premature optimization adds maintenance burden (refresh logic, stale-read risk, migration complexity).

**Simpler approach:** Start with a regular SQL view. If query time exceeds 50ms after Phase 4 data loads, materialize then. The transition is one `CREATE MATERIALIZED VIEW` statement — not worth designing for now.

---

## EXECUTION READINESS

**Can assigned agents execute this as written?** PARTIAL

**Missing (would need to ask before starting):**
1. "Does `freshness_scan_log` have an owner today?" (C2 — 5-min check, should be Phase 0)
2. "What's the timeline for the full burn-down?" (C3 — Peter needs to know when it's green)
3. "How does Phase 4 fit into Varys's existing KE extraction capacity?" (C1 — no throughput model)
4. "What's the answer on view vs matview?" — resolved above: start with view

---

## NOTES

- The plan's reuse strategy is excellent. Using existing `sources` table, existing queues, existing freshness_scan_log is the right approach. The design is lean.
- The Phase 0 gap (freshness_scan_log ownership check) is a 5-minute investigation that should be in Phase 0 or Phase 1, not left as an open question.
- C1 (Phase 4 throughput) is the only genuinely hard problem — 856 entities needs a real extraction plan, not a single line item. The Knowledge Engine plan proves the extraction works; the gap is whether Varys has capacity.
- Daen's 4 open questions are well-framed. Q3 (legislation freshness = content_hash not clock) is correct — answered right in the question text.
- **Patterns from review log applied:**
  - "Single executor across parallel phases without capacity plan" (Knowledge Engine R1) — Phase 4: 856 entities, no throughput model → CONDITIONAL (HIGH)
  - "Timeline-optional plan" (Infra Fix R1) — zero duration estimates across 5 phases → CONDITIONAL (HIGH)
  - "Dormant dependency without ownership check" (Infra Fix R1) — freshness_scan_log ownership unknown → CONDITIONAL (HIGH)
  - "Council question as placeholder" (GCG Work IQ MCP) — Open Question #4 should be answered in the plan, not asked back → MEDIUM
