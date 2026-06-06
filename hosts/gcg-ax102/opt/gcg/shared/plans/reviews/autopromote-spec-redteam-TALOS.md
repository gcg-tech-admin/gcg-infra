# Red-Team Review: Knowledge Engine Autopromote + Currency v1

**Reviewer:** Talos | **Date:** 2026-06-06 | **Spec:** autopromote-currency-spec-v1.md
**Stance:** Adversarial. Assume every component will fail in its worst conceivable way.

---

## 🔴 CRITICAL — Will Ship Bugs to Prod

### 1. Separation-of-Duties Violation: Varys = Scorer + Acquirer

Varys both sources the document AND sits on the 3-panel scoring panel. This is a conflict.

- **Sourced-cost bias:** The agent that found the document has sunk effort in extracting it. Human (and model) psychology is to rate one's own work higher — this is well-documented self-serving bias. Expect Varys's scores to systematically run 3–8 points above the independent reviewers.
- **Perverse incentive:** If Varys wants a fact through the auto-promote 🟢 lane, they can tilt their score up. There is no detection for this. The spread>15 gate catches the extreme case, but a Varys+8 that still keeps spread ≤15 passes unchecked.
- **Fix:** Exclude the acquirer's score from the median, or use a weighted median where the acquirer counts 0.5× and the two independent scorers count 1× each. Document this as a mandatory separation of duties. Alternatively, three-party scoring where NONE of the three is the acquirer — but that means Varys doesn't score at all.

**Severity:** HIGH. Systematic skew erodes the gate.

### 2. Coupled Gates: Vulcan Scores AND QA's

Vulcan has two roles: (a) score the source on the 100-pt rubric, and (b) run value QA (GREEN/RED). These are supposed to be independent gates. They are not independent when the same actor performs both.

- **Anchoring effect:** If Vulcan assigns a source score of 95, they are unconsciously anchored to approve the value QA. A "95 source with RED QA" feels inconsistent — the model will subconsciously find reasons to give GREEN.
- **Inverse anchoring:** A low source score may make Vulcan more likely to RED-tag the value QA ("if the source is bad, this number is probably wrong"), creating a correlated rejection cascade.
- **Fix:** Split — one panel for source scoring (Varys + Talos + Daen), a separate gate for value QA (Vulcan alone). Or: scoring is a 3-panel (acquire, score, QA) where no role repeats. Minimum: document that QA must be done blind to the scores.

**Severity:** MEDIUM. Real, but the impact depends on model conditioning. Worth fixing before build.

---

## 🔴 Architecture Gaps

### 3. Median-of-3 Robustness

Median of 3 with spread>15 → Peter is reasonable but has edge cases:

| Scenarios | m1 | m2 | m3 | Median | Spread | Band | Problem |
|-----------|----|----|----|--------|--------|------|---------|
| Two high, one low | 95 | 94 | 45 | 94 | 50 | Peter | Correct — spread catches it |
| Two medium, one high | 82 | 80 | 95 | 82 | 15 | 🟡 | OK — Peter band |
| **Edge: consensus low** | 68 | 67 | 65 | 67 | 3 | HOLD | Correct — HOLD |
| **Edge: spread=16, median=91** | 91 | 100 | 84 | 91 | 16 | Peter | Works but wastes Peter's time on a likely-good fact |
| **The silent majority problem** | 71 | 89 | 90 | 89 | 19 | Peter | Spread=19 drives to Peter but the real story is 2/3 high |

The last case is the weakest: two scorers think it's borderline auto, one is lower, and Peter gets it because spread is high. That's arguably *correct behavior* — disagreements go to Peter. But it means the "median of 3" formulation is doing less work than it appears. The *spread gate* is the real guard, not the median.

**Recommendation:** Add "at least 2 of 3 agree on band" as a conjunct. This is a simpler, more intuitive rule and covers the same failure modes. If 2-of-3 are in AUTO band and the third is in a lower band, route to Peter. This also eliminates the spread>15 threshold sensitivity.

### 4. Executor Blast Radius — The Single Point of Failure

`promote()` is a single function with the keys to prod. Here's what breaks:

| Failure mode | Impact | Recovery |
|---|---|---|
| Bug in value transformation writes wrong number | Corrupted fact row. Versioning preserves history but CURRENT row is wrong. | Manual SQL rollback: undo `valid_to` on prior, set `valid_from` on current to future. **No automated rollback exists.** |
| Bug in natural-key resolution UPSERTs wrong entity | Data corruption across entity boundaries. | Extremely hard to untangle. Need audit log reverse. |
| Content-hash collision writes identical row | Minor. Duplicate version with same data. Wastes history but not dangerous. |
| GDrive write fails during promotion | Promoted fact exists in DB but no evidence link in GDrive. Violates red-line #4 (orphan fact). | The spec's pseudocode doesn't handle this. `promote()` needs to be transactional: DB write AND GDrive write must both succeed or neither. |
| DB connection drop mid-promote | Partial state — audit log written but fact not in prod. | Idempotency on retry should handle (content_hash match → no-op), but only if the first write didn't partially write. Need transaction wrapping. |

**Required before ship:**
1. **Dry-run mode:** `promote(..., dry_run=True)` — validates all gates, logs what would happen, writes NOTHING. Run for 1 week on staging data before enabling writes.
2. **Rollback script:** `rollback_promotion(promotion_id)` — automated revert of the CURRENT row and restore of prior. Must exist before the first live promote.
3. **Rate limiter:** Max 10 promotions/hour. A runaway loop can still do damage but limits blast radius.
4. **Executor circuit breaker:** If 3 promotions fail in 5 minutes, halt all promotions and alert Peter. Manual reset only.

**Also note:** the executor is the ONLY prod-writer. That's good. But the executor itself has no monitoring — what if it silently crashes? Need a heartbeat check on the executor service and an alert if no promotion has happened in >24h when items are waiting.

---

## 🔴 Implementation Traps

### 5. Changed-Value vs New-Fact Diffing

The spec says content_hash catches duplicates. But changed-value detection is the hard problem:

- **Semantic identity, not lexical:** `0.05` and `5%` are the same value. `"5 percent"` and `"5%"` are the same. A number that changed from 0.05 to 0.06 is a changed value. But what about 0.05 to 0.05000? Trivial formatting difference. Content_hash would differ for both, so both would look like "changed values."
- **Proposed guard:** have the extractor normalize values into a canonical form BEFORE hashing. Publish the normalization rules. A changed-value flag should trigger only when the *normalized representation* differs.
- **Missing from spec:** the changed-value guard for currency. If an in-force DTA is at 0% withholding and a new source says 5%, that's a 🔴 flag that should force 🟡 even at ≥90. The spec mentions this in Part F but doesn't define how the executor detects it. This must be explicit: compare `content_hash` of the incoming row to the `content_hash` of the CURRENT prod row for that natural key. If different → changed value → force 🟡.

### 6. Idempotency Under Race

The spec says content_hash check makes promote() idempotent. In practice:

- **Race condition at Read-Committed:** Two concurrent `promote()` calls both check `SELECT ... WHERE content_hash = X`. Both get zero rows (or both see the same current row). Both pass. Both try to UPSERT. The second UPSERT succeeds and creates a duplicate version row.
- **Per-version idempotency broken:** Two rows with same content_hash and same natural key create two current-version rows. The "current" fact is now ambiguous.
- **Fix:** Use `INSERT ... ON CONFLICT (natural_key, content_hash) DO NOTHING` with a unique constraint on `(natural_key, content_hash, valid_to IS NULL)`. Or use `SELECT ... FOR UPDATE` at the start to serialize. Or run `promote()` under PostgreSQL serializable isolation (with retry on serialization failure).
- **At a minimum:** use `pg_advisory_xact_lock(hash(natural_key))` to serialize per-entity. Cheap, proven, no schema changes.

### 7. Google Alerts Noise — Daily Triage Sink

The daily lane says "Google Alerts + monitored inbox triage." Google Alerts for "UAE corporate tax" returns:

- Every press release (90% irrelevant)
- Every blog post (80% irrelevant)
- Every forum mention (95% irrelevant)
- Duplicate coverage across outlets
- Historical articles on the same topic

**Realistic noise rate:** 90–95%. For a 53-zone, 289-package, 1110-fee intelligence DB, this is hundreds of alerts per day.

**Auto-dismiss heuristics needed, not optional:**
- Known-irrelevant domains: auto-dismiss (blogspot, medium, LinkedIn reposts, news aggregators)
- Duplicate detection: same URL pattern, different domain? Dismiss.
- Minimum confidence threshold: anything the inbox triage scores <40 → discard silently, not even a finding.
- If the daily lane can't auto-dismiss 80%+ of alerts, it becomes a full-time human job.

**Missing from spec:** no auto-dismiss criteria, no false-positive budget/target.

### 8. Backstop Starvation SLA

The weekly targeted search has no explicit SLA. If high-priority items keep filling the slots:

- An in-force DTA at 320/365 days should be the #1 priority. But if there are 50 flagged items, 40 pending-status items, and 30 facts nearing their backstop, *something gets deferred.*
- The spec says weekly search "only on flagged / pending-status / past-backstop items." Past-backstop items should be emergency priority. But what if the queue grows faster than weekly capacity?
- **Required:** A backstop reservation system: every item within 50% of `cadence_days` gets a reserved slot in the weekly run. Items past their backstop deadline get daily priority treatment until resolved.
- **Hard SLA required:** No fact should exceed 1.5× its `cadence_days` without a promotion or admin intervention. If a fact is 1.5× overdue, escalate to Peter with a daily summary.
- **Measurement:** Add `backstop_deadline` (computed at promotion time = now + cadence_days) to the fact table. Query for overdue items in every heartbeat.

---

## 🟡 Additional Issues (Important, not Blocking)

### 9. No Monitoring or Observability on the Pipeline

The executor, scoring panel, Vulcan QA, currency refresh — none has monitoring defined.
- **Missing:** Prometheus/Grafana dashboard: promotions/hour, auto/Peter/HOLD/REJECT counts, median score distribution, Vulcan QA pass rate, queue depth, backstop expiry count, executor error rate.
- **Missing:** Alert on "no promotion in 48h when queue not empty."
- **Missing:** Alert on "Vulcan QA RED rate >30%" (pipeline contamination signal).

### 10. The Peter-Tap Queue Has No SLA

Items that land in 🟡 queue for "one-tap" have no timeout. A fact could sit in Peter's queue for weeks while the source document sits in Drive, never promoted, never currency-checked.
- **Fix:** Add an auto-escalation: any item in Peter-tap for >72h gets re-prompted daily. >7 days gets escalated to Peter via Telegram *and* the superapp.

### 11. GDrive as a Promotion Dependency

The executor writes to GDrive as part of the promotion transaction. GDrive has no write atomicity guarantees with PostgreSQL.
- If GDrive is down, does the promotion stall? If so, the entire pipeline blocks on GDrive availability.
- If the GDrive write fails after the DB write, you have an orphan promoted fact (violates red-line #4).
- **Fix:** Make the GDrive write async (eventual consistency): promote the fact, queue a GDrive upload task, retry on failure. The fact row carries `evidence_link` as NULL until the upload completes. The superapp shows a "pending evidence" indicator.

### 12. Score Temporal Skew

The 3-panel scores happen at different times. Varys might score immediately when sourcing. Talos scores later. Vulcan scores later still. The median is computed from timestamps that could be days apart.
- A source that looked reliable on Monday might have a contradiction discovered on Tuesday. But the median score already computed Monday doesn't reflect it.
- **Fix:** The median should be computed *at promotion time*, not at collection time. If any score is >24h old, re-request it. Or: require all 3 scores within a 1-hour window.

---

## Summary Verdict

| Area | Verdict | Blocks ship? |
|---|---|---|
| Separation of duties (Varys scoring own sources) | 🔴 FAIL | YES — needs fix |
| Coupled gates (Vulcan scores + QA) | 🟡 WEAK | Should fix |
| Median-of-3 + spread rule | ✅ OK | No, but add "2-of-3 agree on band" |
| Executor blast radius | 🔴 FAIL | YES — need dry-run + rollback + rate limit |
| Changed-value detection | 🟡 WEAK | Needs normalization spec |
| Idempotency under race | 🔴 FAIL | YES — needs serialization or advisory lock |
| Alert noise (daily lane) | 🟡 WEAK | Needs auto-dismiss rules |
| Backstop starvation | 🔴 FAIL | YES — needs SLA and reservation |
| Monitoring/observability | 🟡 WEAK | Can add after, but risky |
| Peter-tap queue timeout | 🟡 WEAK | Should add |
| GDrive as sync dependency | 🟡 WEAK | Make async |
| Score temporal skew | 🟡 WEAK | Should add staleness check |

**Ship-blocking issues:** 3 (Varys conflict, executor blast radius, idempotency under race).

**Recommendation:** Fix the 3 critical items, add the "2-of-3 agree on band" rule, define the normalization spec for changed-value detection, and add auto-dismiss heuristics for the daily alert lane. Re-review before Talos builds the executor.
