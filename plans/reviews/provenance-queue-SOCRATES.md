# Socrates Review — provenance-queue (Round 1)

**Plan:** /opt/gcg/shared/plans/provenance-queue-master-plan-2026-06-06.md
**Reviewer:** Socrates (Socratic questioning, assumption probing, contradiction detection)
**Date reviewed:** 2026-06-06

---

## Patterns loaded from review-log (applied below)
1. **Unvalidated extraction reliability** (2026-06-01 infra-fix-registry): extraction must be validated against real data before building infrastructure around it
2. **"If X then Y" where X is unverified** (2026-03-25 LightRAG): downstream phases depend on upstream assumptions
3. **Mechanism verification before claiming** (2026-06-04 talos-heartbeat-oom-fix): verify the mechanism exists before claiming it drives the outcome
4. **Single-executor bottleneck** (2026-06-01 infra-fix-registry): 25+ tasks owned by one agent
5. **Deferred infrastructure costing** (2026-06-06 knowledge-engine): when two phases exist but only one is fully specified

---

## Review

### The good: what this plan earns

- **Goal is precise.** "One board, every entity classified, staleness auto-detected." Measurable, testable, bounded.
- **REUSE doctrine is correct.** `sources` table is the provenance spine. `freshness_scan_log` exists but is dormant — reviving is faster than building. Queue infra, autopromote bands, search_precheck, LEDGER all exist. This is a wire-up, not a rebuild.
- **State machine is well-defined.** Six states with clear transitions and exit actions. No ambiguity about what each state means.
- **Phasing respects the real bottleneck.** P1 (view) and P2 (scanner) are infrastructure. P3-P5 are the extraction work. You see the board before you burn it down.

### What I question

---

#### C1 — The plan has NO extraction reliability validation phase, but Phases 3-4 are 98% extraction work

**The question:** Phases 3-4 involve sourcing 877 entities (36 zones, 841 rows, 138 DTAs, 718 laws). The plan says "Varys extract → Talos stage" and "Varys → Talos" — but there is no Phase 0 or Phase 0.5 that validates Varys can actually extract from the target sources.

**Why the plan can't answer it:** The plan describes the destination (sourced↔linked↔scored) and the mechanism (sources table + freshness_scan_log + view), but never validates the extraction pipeline. We know:
- 36 zero-data freezone zones need extraction from 17 different zone websites (per source-doc-system, varying site structures)
- 138 unsourced DTAs need extraction from MoF PDFs/gazettes (document delivery of MoF which is...)
- **718 legislation rows with ZERO sourced** — entire table has no provenance. No existing extraction pipeline. The hardest domain with the most rows gets P4, after pricing.

**What it means (CRITICAL):** If Varys cannot reliably extract legislation texts (718 rows) or DTA PDFs (138 treaties), Phases 3-4 collapse regardless of how good the provenance view is. The view shows a board; the extraction puts data on it. Extraction unreliability at the scale of 877 entities is not a risk — it's the central execution risk of this plan, and it has no validation gate.

**What would satisfy me:** A Phase 0.5 that proves extraction works for each domain before the burn-down phases:
- Freezone pricing: validate against the 3-5 easiest zone sites first
- DTAs: validate against MoF PDF availability (not all 152, but the first batch)
- Legislation: validate against FTA/gazette accessibility for at least 5-10 laws
- A gate that says "extraction pipeline validated against real targets for X entities" before P3 starts

---

#### C2 — `freshness_scan_log` has NO owning service, and extending it without knowing how it was supposed to run creates a phantom phase

**The question:** Daen's open question #4 asks "does the unused freshness_scan_log have an owning service already?" This is framed as a question, but the plan gives no answer and proceeds as if the table is free to extend.

**Why the plan can't answer it:** The plan says "extend `freshness_scan_log`" but does not specify:
- What service was supposed to write to it (cron job? agent? middleware?)
- What the expected write pattern was (per-scan? per-source? per-change?)
- Whether the existing schema is compatible with the extension or needs migration
- Whether the "revive" is a cron job or an agent turn

**What it means (CRITICAL):** The freshness scanner is the entire staleness-detection mechanism for Phase 2. If `freshness_scan_log` was never wired to a real service, then "extending" it means building the scanner from scratch, just reusing a table name. That's not reuse — that's starting from schema. The plan says "revive/extend it, don't make a new one" but if there's nothing to revive, Phase 2 has zero existing infrastructure.

**What would satisfy me:** Before Phase 2, answer open question #4 with a concrete finding:
- `\d+ freshness_scan_log` to confirm schema
- Check crontab/systemd/gateway jobs for anything referencing it
- Check dead-letter logs or error tables for insert attempts
- If nothing writes to it: rename the table to reflect the new scanner and build the feed from scratch, documented as new, not "extend"

---

#### HIGH — Single-executor bottleneck on the hardest extraction domains

**The question:** Phases 3-5 are assigned to "Varys extract → Talos stage" for pricing, then "Varys → Talos" for tax/legal, then "Varys advisory + Vulcan binding" for scoring. Varys owns every extraction step across 877 entities.

**Why it matters:** The plan assumes Varys has capacity to:
- Extract 36 zones + reconcile 841 rows (P3)
- Extract 138 DTAs + 718 laws (P4)
- Advise on 51 unscored sources (P5)

This is ~1,500 extraction/reconciliation operations. If Varys handles 10 entities per day (generous for DTAs that need full reconciliation), P3-P4 covers ~88 working days. There is no stated throughput estimate.

**What would satisfy me:** A throughput estimate per domain type:
- "Pricing: X zones/day" (simple)
- "DTAs: Y treaties/day" (reconciliation-heavy)
- "Legislation: Z laws/day" (hardest)
- If total calendar exceeds 30 days, name the bottleneck explicitly

---

#### HIGH — Legislation (718 rows, 0% sourced) has no extraction strategy

**The question:** 718 legislation rows with zero provenance. The plan says "mostly new run vs MoF/FTA/gazette." What kind of legislation? Federal laws? FTA circulars? Ministerial decisions? Gazetted vs non-gazetted?

**Why the plan can't answer it:** The plan doesn't describe the legislation domain at all. The baseline table says "FTA/federal" but:
- Are these VAT law articles, corporate tax regulations, AML decrees, or sector-specific?
- Are they publicly available on the FTA website, or do they require a legal database subscription?
- Do they change frequently (legislative amendments) or rarely (constitutional)?
- Is the entire 718-table-reconciliation even in scope, or is the first pass just the 100 most-used?

**What would satisfy me:** A legislation domain audit as Part of Phase 4 or a Phase 0.5:
- Sample 10-20 rows, verify the source URL/PDF exists
- Classify the domain into sub-types (laws, regulations, circulars, cabinet decisions)
- Estimate extraction effort per sub-type
- Gate: "confirmed 718 rows can be sourced" before P4 enters burn-down

---

#### MEDIUM — `last_fetched_at` populated for only 5/85 sources, but the view depends on it

**The question:** The view computes days-to-stale as `cadence_days − age(last_fetched_at)`. If last_fetched_at is NULL for 80/85 sources, the view will show NULL days-to-stale for 94% of sources.

**Why it matters:** The plan acknowledges this ("only 5 populated — gap") but doesn't say how the view handles NULL `last_fetched_at`. If the view logic treats NULL as "no data" and assigns `no_data` or `unsourced`, the view will be mostly useless until Phase 2 backfills last_fetched_at. If it treats NULL as "never fetched" and assigns `scored_stale`, it will incorrectly flag verified sources as stale.

**What would satisfy me:** Explicit view logic for `last_fetched_at IS NULL` — and confirmation that Phase 2's first task is to backfill last_fetched_at for all 85 sources before the scanner runs.

---

#### MEDIUM — Autopromote bands pre-suppose scoring infrastructure that Phase 5 doesn't schedule

**The question:** The plan defines autopromote bands (🟢≥90 AUTO / 🟡70–89 PETER-TAP / 🟠 HOLD / 🔴 REJECT) in the design section, but Phase 5 (score + promote) is last — after all extraction is done.

**Why it matters:** The bands reference a scoring mechanism (Varys advisory → Vulcan binding), but there's no scoring pipeline defined anywhere. The scoring is supposed to happen in Phase 5, but the autopromote mechanism is described as infrastructure that exists. Does it? Or is Phase 5 building the scoring pipeline AND scoring 51 sources simultaneously?

**What would satisfy me:** Clarify whether the scoring pipeline exists today (is it the existing rubric in `source_database.md`?) or whether Phase 5 is building it anew. If the latter, it needs a task list bigger than "Varys advisory + Vulcan binding."

---

#### MEDIUM — No roll-back or rework loop defined

**The question:** When the view classifies an entity wrong (e.g., marks a verified DTA as `no_data` because the join missed), how does the correction propagate?

**Why it matters:** The plan defines six forward transitions (no_data → ... → verified_current) but no backward edges or correction flows. If the scanner flips a source to `scored_stale` incorrectly (false positive hash change), how does it get restored?

**What would satisfy me:** A one-paragraph note on how corrections and false positives are handled — even if it's just "manual override in sources table triggers recompute."

---

## Recurring patterns from review-log that did NOT trigger a finding
- Phasing is correct (P1-P2 infra before P3-P5 extraction) — avoids the "pipeline measures output, not ROI" pattern from gcg-ai-seo-domination ✅
- "If X then Y" chains are well-contained (P1+P2 don't depend on P3-P5 data) ✅
- Extraction dependency is flagged but the dependency order is correct ✅
- Single-executor is the bottleneck but it's stated honestly, not hidden ✅

---

## VERDICT: CONDITIONAL

### CRITICAL FINDINGS (block deployment):
- **C1 — No extraction reliability validation before Phases 3-4 burn-down.** 877 entities (36 zones, 841 rows, 138 DTAs, 718 laws) need extraction from live targets. Zero Phase 0 validation that Varys can extract from the target sources. The view and scanner are correct but deliver no value until extraction works. Add a Phase 0.5 that validates extraction pipeline against 5-10 real entities per domain before P3 starts.
- **C2 — `freshness_scan_log` "extend" claim assumes an owning service exists.** The plan must verify `freshness_scan_log` is actually populated and has a service owning it. If nothing writes to it, rename and build from scratch. Phase 2's staleness-detection mechanism depends on this. Add a schema audit and service hunt before Phase 2.

### HIGH FINDINGS (must fix within 2 days):
- **H1 — Single-executor bottleneck on hardest domains.** Varys owns 1,500+ extraction/reconciliation operations across P3-P5 with no throughput estimate. Provide a per-domain throughput estimate to validate the timeline.
- **H2 — Legislation domain (718 rows, 0% sourced) has no extraction strategy.** "MoF/FTA/gazette" is not a plan. Classify the 718 laws into sub-types, verify source availability for each, estimate effort. Gate P4 on this audit.

### MEDIUM FINDINGS (advisory):
- **M1 — `last_fetched_at` NULL for 80/85 sources makes the view logic ambiguous.** Define view behavior for NULL `last_fetched_at` explicitly. Backfill in Phase 2 as first action.
- **M2 — Autopromote bands reference scoring infrastructure that may not exist.** Clarify whether scoring pipeline exists today or Phase 5 builds it.
- **M3 — No correction flow for false positives.** How does a wrongly-classified entity get corrected? Document even a one-line answer.
- **M4 — Legislation sub-domain classification recommended.** 718 rows is the hardest extraction lift; a sub-type classification would make the plan concrete.

### NOTES:
- The plan's design is correct. The questions are about execution gaps, not architecture.
- The REUSE discipline is exemplary — `sources` table as spine, existing queues, existing bands. This is the quality of plan I want to see more of.
- The four design decisions (matview, single normalized view, cadence defaults, scan_log ownership) are all correct pending the scan_log audit.
- This is a CONDITIONAL, not a FAIL — the plan is structurally sound, it just needs to validate its execution assumptions before committing to burn-down.
