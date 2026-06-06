# Red-Team Review: Knowledge Engine Autopromote + Currency Build Spec v1

**Reviewer:** Daen (The Architect)  
**Date:** 2026-06-06  
**Spec:** `/opt/gcg/shared/plans/knowledge-engine-autopromote-currency-spec-v1.md`  
**Status:** 🟡 BLOCKING — several issues must be resolved before Talos builds.  
**Intent:** Adversarial, not agreeable. I want this to survive production.

---

## 1. Self-scoring conflict: Varys acquires AND scores

**Verdict:** 🔴 Real conflict. Needs mitigation.

Varys does the acquisition — chooses which sources to fetch, formats them, brings them into staging. Then Varys also sits on the 3-panel scoring board. This is a textbook rater-acquirer conflict.

**Why it matters:**
- Unconscious anchoring: Varys picked the source; it's psychologically harder to then score it as low-quality. The scoring rubric is meant to be objective, but rubrics don't eliminate confirmation bias.
- Strategic incentive: Varys's "throughput" — the number of sources it finds and successfully promotes — is a direct function of its scores. Low-scoring sources waste effort. There's an unstated motive to inflate.
- Audit trail problem: If a borderline source (58/100) gets scored 82 by Varys, how do we distinguish "honest disagreement" from "advocacy"?

**Mitigations:**
1. **Downweight Varys's score for sources it acquired.** Easiest: when `source.acquired_by = 'varys'`, Varys's score is excluded from median calculation (2-panel for self-acquired). The score is still logged for audit but doesn't influence the gate.
2. OR: **Flip the scoring model.** Varys acquires only. Scoring is done by Talos + Vulcan + a rotating third from the Council (pick one, non-collusive). This removes the conflict entirely.
3. MINIMUM: Add a `source.acquired_by` field. Log it. The promotion bundle should clearly show whether the acquirer scored higher than the other two, so Peter can spot patterns.

**Recommendation:** Option 1 is light-touch. Option 2 is architecturally clean. Go with 2 if you want this to be trusted.

---

## 2. Vulcan double-role: Scorer AND value QA

**Verdict:** 🟡 Coupled, but survivable with one constraint.

Vulcan has two distinct responsibilities:
- **Role A:** Score the source on the 100-pt rubric (sits alongside Varys and Talos)
- **Role B:** Value QA — confirm staged value matches source value (GREEN/RED)

**Why it's a problem:**
- If Vulcan gave a source a high score (e.g., 95), it now QA-checks the same source's extracted values. The high score creates an anchoring effect — "this source is good, probably the value is right too."
- Conversely, a low-scoring source subconsciously raises QA suspicion.
- The two operations are meant to measure orthogonal things (source credibility vs. extraction fidelity), but sharing a rater couples them.

**Mitigation:**
- **Separate the QA role entirely.** QA is a mechanical check — does the number in staging match the number in the source document? This doesn't need a full agent reasoning loop. It can be a deterministic script or a Haiku subagent with no access to the score. 
- If Talos implements Vulcan QA as a lightweight, deterministic comparison against source text, the double-role concern vanishes because there's no scoring judgment in QA. But if Vulcan QA involves any judgment ("does this treaty clause really mean what the extractor says?"), then it's Role C and the coupling compounds.

**Recommendation:** Define QA as pure mechanical value-match. If it involves interpretation, give it to a different agent (or a stateless tool). Document this clearly in the spec before Talos starts.

---

## 3. Median-of-3 robustness vs. all-3-must-agree-on-band

**Verdict:** 🟡 Median is acceptable with a tighter spread rule. Current spread>15 is too wide.

**Analysis:**
- With spread ≤ 15, the three scores are within 15 points. For the AUTO band (median ≥ 90), this means all three scores are ≥ 75 at minimum. That's reasonable but not tight.
- Example A: Scores 75, 90, 90 → median 90, spread 15. One scorer thinks the source is mediocre (75). The other two say great. Is this really AUTO-promotion material? The dissenter is at the HOLD band threshold.
- Example B: Scores 85, 88, 90 → median 88, spread 5. This goes to Peter (70-89). Good — the spread is low but the median is below AUTO. Correct handling.
- Example C: Scores 92, 92, 45 → median 92, spread 47. One outlier tanks it → Peter. Correct.

**The edge case that worries me:** 80, 95, 95 → median 95, spread 15. The 80-scorer is honest; the other two overscored. That fact auto-promotes with one dissenter at 80. Is spread>15 the right threshold? I think **spread > 10 should route to Peter for AUTO-band candidates**, not >15. A 10-point gap in a 100-point scale is already significant disagreement.

**On "all-3-agree-on-band" specifically:** This is too strict. If scores are 89, 91, 93 → median 91 (AUTO band) but the 89 (PETER band) would force a Peter-tap despite near-consensus. This would create false-positive escalations. Median + spread is the right mechanism; just tighten spread to 10.

**Recommendation:** Change spread threshold from >15 to >10 for AUTO-band routing. Leave >15 for lower bands.

---

## 4. Executor blast radius + rollback + dry-run

**Verdict:** 🔴 Insufficient safeguards. Must fix before Talos builds.

**Blast radius analysis:**
The executor (`promote()`) can UPSERT any row in prod by natural key. The worst-case bug:
- **Wrong value write:** UPSERT with content_hash that doesn't match → overwrites a correct prod fact with a wrong staging value. Since content_hash is checked post-promotion in the bundle but NOT used as a pre-condition guard in the spec (checked idempotently), a bug could write garbage.
- **Mass promotion:** If the staging->prod cursor accidentally selects everything, every staged row gets promoted in one batch, including HOLD candidates.
- **Metadata corruption:** `valid_from`, `valid_to` stamping wrong → timeline corruption. Closing a version that shouldn't be closed.

**Required before building:**

1. **Dry-run mode — MANDATORY first deployment phase.**
   - `promote()` runs for 7 days in dry-run only. It logs what it WOULD have promoted, with before/after diff. Peter reviews the log. No prod writes.
   - Dry-run flag is a hard env var: `AUTOPROMOTE_DRY_RUN=true`. Until this is explicitly removed (Peter approval), nothing touches prod.

2. **Rollback mechanism — build this into the versioning.**
   - Every promotion creates a new version row (`valid_to` = now on old, new row inserted). This is good.
   - Add a `rollback(content_hash)` function: find the row, close the current `valid_from`, re-open the prior row's `valid_to`. This needs to exist and be tested before promotion goes live.
   - Add a time-bounded batch rollback: "roll back all promotions in the last hour."

3. **Batch guard — hard limit per invocation.**
   - `promote()` must have a per-call max (e.g., 50 rows). A bug that tries to promote 10,000 rows hits the guard and fails. Logs the attempt.
   - This prevents accidental mass promotion even if the cursor is wrong.

4. **Circuit breaker.** If 3 consecutive promotions fail validation (e.g., content_hash mismatch), shut down the executor and alert Daen. No automatic retry.

**Recommendation:** Blocking. Deploy in dry-run only for minimum 7 days. Batch guard and rollback function are non-negotiable.

---

## 5. Changed-value vs. new-fact diffing + idempotency under race

**Verdict:** 🟡 Partial design, missing key details.

**The core problem:** Two fundamentally different operations have the same trigger (new staged data):

| Scenario | Detection | Correct action |
|---|---|---|
| Same source, re-fetched with same value | content_hash matches → no-op | Skip (idempotent) |
| Same source, re-fetched with new value | content_hash differs, natural key matches | Update (force 🟡, re-stamp) |
| New source, new fact, same natural key | Different source_id, different content_hash | New fact? Or contradiction? |

**Gaps:**
- Natural key definition isn't specified. Is it `(treaty_name, article, jurisdiction)`? What about `(dta_country, rate_type, effective_date)`? Need a schema.
- **Race condition:** Two findings about the same fact land in staging within seconds (e.g., Google Alert + inbox triage both trigger on the same news article). The executor runs twice:
  - First run: promotes, sets content_hash_A, stamps valid_to on old row.
  - Second run: sees the same fact, same natural key, different content_hash_B (because the two findings interpreted it differently). What happens? It UPSERTS a new version. Now we have two competing versions. The versioning handles this technically (valid_from/valid_to don't overlap), but **which version is right?**
- Fix: Before promotion, check if **any other staging row** with the same natural key has been enqueued but not yet processed. Queue a reconciliation instead of auto-promoting either.

**Recommendation:** Define natural key schema explicitly. Add a dedup stage before promote() that merges multiple staging rows for the same natural key into a single candidate (with a conflict flag if values differ).

---

## 6. Changed-value guard forces 🟡 — but what triggers a "re-fetch"?

**Verdict:** 🟡 Right idea, under-specified.

The spec says a changed number on an in-force fact forces 🟡 even at ≥90. Good instinct. But:

- **Who detects the change?** Is it the daily triage lane noting "this page looks different"? Or the backstop finding the source updated? 
- **How do we distinguish "source updated" from "new source with same fact"?** If a DTA treaty page gets redesigned (same content, new URL), does the engine think it's a change?
- **What about rounding differences?** Old value: "15.0%". New extraction: "15%". Is that a change? Need a normalization layer before diffing.

**Recommendation:** Add a `normalized_value` field alongside raw `value`. Use normalized for diffing. Document the normalization rules per data type (percentage, currency, date, text). This should be a shared utility function, not ad-hoc.

---

## 7. Alert noise + daily triage sustainability

**Verdict:** 🔴 Underestimated. This will become a time sink without aggressive filtering.

**The problem:**
- Google Alerts for "UAE corporate tax rate 2026" generates stories when a journalist tweets about it, when a law firm blogs about it, when someone quotes a partner, etc. 95%+ false-positive rate for factual changes.
- Monitored inbox: at least one jurisdiction-related email per day. Half are irrelevant.
- Daily "no search" triage means a human (Peter) or an agent is reading through all of this every day.

**Auto-dismiss heuristics — need these BEFORE going live:**
- **Source credibility filter:** Is the source from a government domain, a gazette, an official notification? If not, flag but don't auto-create a finding.
- **Temporal proximity:** Same alert about the same topic within 7 days → merge, don't create new finding.
- **Content-change threshold:** Does the diff from the last known state change an actual fact field, or just text/summary/opinion? Junk diffs auto-dismiss.
- **Agent triage SLA:** If an auto-dismiss heuristic is uncertain, route to Varys (not Peter). Let Varys batch-review daily at a scheduled time. Peter should only see alerts that pass Varys's triage gate.

**Recommendation:** Add a Varys triage stage between "alert received" and "finding created." Otherwise this daily loop consumes 30+ minutes of Peter's time. That violates the entire point of the engine.

---

## 8. Backstop starvation — hard SLA

**Verdict:** 🟡 Missing explicit escalation. Easy to fix.

The backstop cadence is defined (`cadence_days` per category) but the mechanism only says "weekly targeted search." What happens when an item is continuously deferred?

**Scenarios:**
- A pending DTA (cadence_days = 7) keeps getting outranked by higher-priority items in the weekly targeted search. After 3 weeks, it's 14 days past SLA. Who notices?
- A tracked freezone fee with cadence_days = 30 hasn't been re-scraped in 45 days because the scraping site was down. No one flags it.

**Fix:**
- **Hard overdue flag:** A view that computes `(now - last_verified) > cadence_days * 1.5` and flags the row as OVERDUE. Overdue rows get escalated to Peter-tap queue automatically.
- **Backstop queue priority:** The weekly targeted search MUST process overdue items before anything else. Not "first" — MANDATORY. If any overdue items exist, they consume the week's search budget before any non-overdue work.
- **Alert if overdue exists:** If any item exceeds 2× cadence_days, the executor sends an alert to Daen. If any exceeds 3×, alert Peter.

**Recommendation:** Implement overdue flag + forced priority processing + escalation. Simple, prevents silent staleness.

---

## 9. Unaddressed issues the spec doesn't ask about

### 9a. Source invalidation (post-promotion lifecycle)
A promoted fact's source can change (website goes down, treaty is amended, the URL 404s). The current model has no "source degraded" signal. 
- If source is unreachable at the next backstop check → flag the fact as `source_unreachable`, force 🟡, alert for re-fetch.
- Add a `source_health` column: `HEALTHY / STALE / DEAD`. Dead sources get flagged to Peter.

### 9b. Scoring panel coordination
The spec says "each scores independently, alone, no collusion." But who triggers the scoring? Who waits for all three? If one scorer is slow, does the pipeline block?
- Need a coordinator: when a new source arrives, enqueue a scoring task. Each scorer completes independently. When all three have scored, trigger aggregate + banding.
- Timeout: if a scorer hasn't responded within 24h (for bulk) or 1h (for urgent), their score defaults to NULL and median falls to 2-panel. Log the timeout.
- Without this, a single stuck scorer blocks the entire pipeline.

### 9c. Peter-tap queue TTL
What happens to items in the PETER band (70-89) that Peter never looks at? They sit in the queue indefinitely. Missing facts never reach prod.
- Add a `auto_promote_if_no_response` TTL (e.g., 14 days). If Peter hasn't actioned a row in 14 days, it auto-promotes with a warning flag. Better to have a possibly-mediocre fact in prod than a hole in the knowledge base.
- OR: auto-demote to HOLD after 30 days, with a notification.

### 9d. Multiple sources, same fact
If two independent sources both provide the same fact value, which source_id does the promoted row carry? 
- Spec says "every promoted row carries source_id" — but a fact could be corroborated by 10 sources (corrob ≥ 10). Which one is THE source?
- Fix: promote with `primary_source_id` + `corroborating_source_ids[]`. The primary is the highest-scored source among the 10. The others are listed for audit.

### 9e. Peter one-time sign-off is ambiguous
The red-lines say "executor ships only after Daen QA + Peter's one-time sign-off." But the autopromote spec implies AUTO-band bypasses Peter entirely for individual facts. 
- Is the "one-time sign-off" an initial enablement authorization, or does Peter sign off each auto-promotion batch? 
- If it's the former, red-line #1 is satisfied. If it's the latter, it contradicts Part B's AUTO lane. Clarify.

---

## Summary verdict

| Area | Severity | Verdict |
|---|---|---|
| 1. Varys self-scoring conflict | 🔴 Blocking | Acquired sources need 2-panel score, not 3 |
| 2. Vulcan double-role | 🟡 Moderate | OK if QA is mechanical; fix if interpretive |
| 3. Median robustness | 🟡 Minor | Tighten spread to 10 for AUTO band |
| 4. Executor blast radius | 🔴 Blocking | Dry-run 7d + batch guard + rollback func required |
| 5. Changed-value & race | 🟡 Moderate | Define natural key + dedup before promote |
| 6. Change detection | 🟡 Minor | Add normalization layer |
| 7. Alert noise | 🔴 Blocking | Varys triage gate needed or daily loop consumes Peter |
| 8. Backstop starvation | 🟡 Moderate | Overdue flag + escalation, easy fix |
| 9a. Source invalidation | 🟡 Moderate | Add source_health column |
| 9b. Scoring coordination | 🟡 Moderate | Coordinator + per-scorer timeout needed |
| 9c. Peter-tap TTL | 🟡 Minor | Auto-promote after 14d silence |
| 9d. Multi-source | 🟡 Minor | primary_source_id + corroborating array |
| 9e. One-time sign-off | 🟡 Minor | Clarify scope of "one-time" |

**Overall: 🟡 BLOCKED from build.**  
Three blocking issues (1, 4, 7). Fix those first, then Talos can build with confidence. The rest are minor-to-moderate and can be iterated during dry-run.

**Recommended order:** Address blast radius safeguards (4) → define Varys scoring rules (1) → build Varys triage gate (7) → everything else can follow.

---

*Daen, 2026-06-06. Adversarial, zero sugar-coating. This is the first automated prod-write path GCG has ever built. Get it right.*
