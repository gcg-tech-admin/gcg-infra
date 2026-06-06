# Red-Team: Knowledge Engine Autopromote + Currency Spec v1
> Reviewer: Vulcan | Date: 2026-06-06 | Requested by: Varys (Peter-ordered)
> Posture: adversarial. Findings are attack surfaces, not suggestions.

---

## SEVERITY LEGEND
- 🔴 **CRITICAL** — breaks a red-line or causes silent data corruption
- 🟠 **HIGH** — causes incorrect promotions or undetectable failures
- 🟡 **MEDIUM** — degrades reliability or creates operational burden at scale
- 🔵 **LOW** — design debt, edge cases worth spec-ing now

---

## The 8 Red-Team Questions

### Q1. Varys acquires AND scores — self-scoring conflict

**Rating: 🟠 HIGH**

Varys chose this source. That choice is already a vote of confidence. Now Varys gets 1 of 3 scoring votes on whether it clears the bar. The conflict is structural, not hypothetical.

Concrete attack: Varys surfaces a T2 source they believe is high quality. They score it 93. Talos scores 88. Vulcan scores 80. Median = 88, spread = 13. Routes to 🟡 correctly — but only because Talos was conservative. If Talos had scored 91: median = 91, spread = 13 → **AUTO-PROMOTE from a T2 source that slipped through**.

The spec has no acquirer-recusal rule. The acquirer has the most context on the source (they found it) but also the strongest motivation to justify the acquisition. These are both true simultaneously — and they cut in opposite directions. The spec assumes scorer independence it cannot guarantee.

**Fix required:** Acquirer's score should be advisory-only (shown but not counted in median). Or: acquirer is replaced with a 4th neutral scorer for that row. At minimum, spec must acknowledge this as an accepted risk with Peter sign-off — not ignore it.

---

### Q2. Vulcan double-role: source scorer AND value QA — coupled gates

**Rating: 🟠 HIGH**

The spec says these are "distinct" — but distinct execution steps do not mean independent failure modes.

The attack: Vulcan misreads a treaty document during source scoring (rates section is ambiguous, Vulcan scores it 92). The same misread carries into value QA — because both operations are grounded in the same (incorrect) reading of the same document. Vulcan scores GREEN on both. The wrong number enters prod without contradiction.

This is not hypothetical. The value of two gates is that they catch each other's blind spots. Two gates operated by the same agent, reading the same source, with the same knowledge cutoff and the same potential misunderstanding, do not provide independent coverage. They provide the illusion of it.

The spec also doesn't prevent Vulcan from caching the source read across both operations. If the source document is fetched once and reused, any parsing error propagates into both the score and the QA.

**Fix required:** Value QA must be performed by an agent who did NOT score the source for that row. Talos is already on the panel and could run value QA for rows where Vulcan scored. Alternatively: value QA should be run against the raw source document with a fresh fetch, never a cached read from the scoring pass.

---

### Q3. Median-of-3 robustness vs. all-3-agree-on-band

**Rating: 🟡 MEDIUM with a 🟠 tail**

The spec's spread>15 guard catches gross disagreement. It does NOT catch meaningful dissent within the spread threshold.

Attack scenario: Varys=95, Talos=91, Vulcan=78. Spread=17 → correctly routes to Peter.
But: Varys=95, Talos=90, Vulcan=81. Spread=14 → median=90 → **AUTO-PROMOTE**. Vulcan's 81 is a 🟡-band score (70–89). One scorer said "this needs human review" and was overruled by median arithmetic.

The spec is optimizing for precision (median is statistically robust) over safety (unanimous band agreement would prevent this). For the **first automated prod-write path in GCG**, this tradeoff should be explicit — not an implementation detail.

**All-3-agree-on-band** is a higher bar: all three must independently reach ≥90 before AUTO fires. This catches the "two high scorers override a legitimate dissent" case. The cost: more 🟡 routes, more Peter taps. For v1 of an autopromote system that hasn't earned trust yet, that cost is appropriate.

**Recommendation:** For v1, require all-3-agree-on-band (all ≥90) for AUTO. Relax to median+spread after the executor has a track record.

---

### Q4. Executor blast radius + rollback + dry-run

**Rating: 🔴 CRITICAL**

The spec describes what `promote()` does. It does not describe:
- What happens when it goes wrong at scale
- How you detect that it went wrong
- How you undo it

**Blast radius scenarios:**

1. **Loop bug:** a misconfigured trigger fires `promote()` on 500 rows in a batch. All 500 UPSERT into prod before anyone notices. The spec has no per-run row cap, no alarm on "N promotions in T minutes."

2. **Natural key collision bug:** the UPSERT key logic is wrong. Two distinct facts (e.g., UK-France DTA withholding rate vs UK-France DTA capital gains rate) share a computed natural key due to a code bug. One fact silently overwrites the other. Both have the same `content_hash` after the overwrite. Audit trail shows a promotion. No error.

3. **GDrive write fails after DB commit:** the DB UPSERT completes. The GDrive evidence link write fails. Red-line #4 says no orphan facts. You now have an orphan fact. The transaction is already committed. The spec has no compensating transaction or retry queue for this.

4. **`valid_from`/`valid_to` stamping race:** two promotions of the same fact from different sources fire within the same second. Both write `valid_to = NULL` on the prior version. One wins, one creates a broken version chain. No DB constraint prevents this.

**Missing from spec:**
- No dry-run mode (required for Daen QA before Peter sign-off — how does Daen validate without running against prod?)
- No max-rows-per-batch guard
- No circuit breaker ("if >N promotions in M minutes, halt and alert")
- No rollback procedure for a bad batch
- No atomicity guarantee between DB write and GDrive write

**Fix required:** Spec must define: dry-run mode, per-batch row cap with hard alert, circuit breaker threshold, rollback SOP for bad batches, and an ordering contract (GDrive link written FIRST, then DB committed with that link — not the reverse).

---

### Q5. Changed-value detection — natural key and diffing reliability

**Rating: 🟠 HIGH**

The spec says "changed-value guard: a changed number forces 🟡 even at ≥90." This assumes reliable detection of "same fact, new value." The spec never defines the natural key rules that make this detection possible.

**Attacks:**

1. **Undefined natural key:** for DTA agreements, is the natural key `(country_pair, effective_date, rate_type)`? `(country_pair, agreement_type, article_number)`? If Talos gets this wrong, you get either false collisions (two distinct facts treated as one updated fact) or missed collisions (an amended treaty treated as a new fact, bypassing the changed-value guard entirely).

2. **Same-value false negative:** the source document was reformatted, a footnote was added, the PDF was re-scanned at higher resolution. `content_hash` changes. The system treats this as "changed value." Changed-value guard fires. Peter gets a tap for a fact that didn't actually change. At scale, this generates alert fatigue in the Peter tap queue.

3. **Content hash without normalization:** if `content_hash` is computed on raw source text, whitespace/encoding changes trigger the guard on unchanged facts. If computed on extracted values, the normalization layer can mask real changes (e.g., "15%" vs "15.0%" treated as same).

4. **Changed-value detection timing:** does the detection happen before or after scoring? If after, a changed-value fact can be scored, hit ≥90, pass Vulcan QA, and then the changed-value guard fires at the executor. This is correct behavior — but it means a fact can be fully processed before the routing decision is made, creating a window where the changed-value guard could be bypassed if the executor logic has a bug.

**Fix required:** Define the natural key per fact table explicitly in the spec before Talos builds. Define what "value" means for changed-value diffing (extracted structured field, not raw hash). Define the normalization contract.

---

### Q6. Alert noise — Google Alerts false-positive rate

**Rating: 🟡 MEDIUM**

"~free" understates the operational cost. Google Alerts for tax treaty keywords will surface:
- Political commentary on treaty negotiations
- News articles mentioning existing treaties in passing
- Duplicate alerts for the same event from different outlets
- Alerts for countries in the DB that have no actionable updates

The spec provides no auto-dismiss heuristics. The daily triage lane says "touches a tracked entity? → finding." A news article mentioning "UK-France tax treaty" in a Brexit retrospective touches a tracked entity. It generates a finding. Someone triages it. Finding is noise.

**The starvation risk from noise:** if daily triage generates 30 findings/day and 25 are noise, the 5 real signals get buried. Triage fatigue means the 5 real signals get deferred. The currency layer's "~free" claim evaporates when the noise-to-signal ratio makes the daily lane more expensive than weekly targeted search.

**Missing:** minimum-signal threshold for creating a finding (not just "entity mentioned"), auto-dismiss rules for duplicate sources, lookback deduplication (same alert same entity within 7 days = suppress).

---

### Q7. Backstop starvation — hard SLA enforcement

**Rating: 🟠 HIGH**

The spec defines backstop cadences (365/7/90/30–60 days). It does NOT define what happens when a cadence is missed. "Weekly targeted search on past-backstop items" is the mechanism — but the spec gives no guarantee this search fires reliably, no escalation path if it doesn't, and no hard alerting if an item is past-backstop by >X days.

**The starvation scenario:** freezone fees have a 30-day backstop. Vulcan's weekly search runs but the freezone fee item is consistently low-signal — no new Google Alerts, no events in inbox. The targeted search de-prioritizes it because there's nothing to find. The item sits at 45 days, 60 days, 90 days since last verification. No one notices because the system is event-driven and there's no event.

Event-driven architectures are efficient on the happy path. They fail silently on stale data. This spec is almost entirely event-driven, with backstop cadences as the only time-based safeguard — but there's no enforcement mechanism for those cadences.

**Fix required:** A separate, non-deferrable cron job that queries: `WHERE last_verified < now() - cadence_days AND status = 'in_force'`. If found → hard escalation to Peter/Varys, regardless of whether the currency layer has pending work. This is not the currency layer — it's the catch-all that fires when the currency layer has failed to act.

---

### Q8. Idempotency under race — double promotion

**Rating: 🟠 HIGH**

The spec says the executor is idempotent at same `content_hash`. This protects against retry-on-success. It does NOT protect against the parallel-execution race.

**Attack:** Two Google Alert findings arrive 30 seconds apart for the same source (e.g., a treaty PDF was re-indexed by Google). Both create findings. Both enter the 3-panel scoring queue. Both score ≥90. Both reach the executor simultaneously. Each executor instance checks "already promoted at this content_hash?" — both check the DB, both see NO (the first hasn't committed yet). Both proceed. Both UPSERT. One wins at the DB level. The other writes a duplicate promotion bundle to GDrive.

The DB UPSERT is last-write-wins — idempotent in terms of final data state. But:
- Two GDrive evidence links are written for one fact
- Two `last_verified` stamps fire
- Two audit log rows are created
- If there's a notification on promotion, two notifications fire

More seriously: if the two findings had slightly different `content_hash` (e.g., different fetch timestamps in the source metadata), neither is a no-op. One supersedes the other, writing a `valid_to` on a fact that was promoted 100ms ago. You now have a version chain where a fact was "in force" for 100ms before being superseded. This will cause confusion in audit trails and may confuse consumers of the versioned fact.

**Fix required:** DB-level advisory lock or `SELECT FOR UPDATE` on the staging row before executor starts. Only one executor can process a given `(staging_row_id OR natural_key)` at a time. Alternatively: a work queue (not parallel triggers) so only one executor ever processes a finding at a time.

---

## Additional Issues Not in the 8 Questions

### A. No burn-in period before full auto-promotion 🔴 CRITICAL

The spec says Peter signs off once on the executor design, then automation runs. But the first N auto-promotions will be the first time the system operates at full speed on real data. There is no graduated trust model.

**Recommendation:** First 30 auto-promotions (or first 30 days) should generate "shadow promotions" — the executor runs, writes to a `staging_promoted` shadow table, and alerts Peter with "would have promoted X." Peter reviews. After 30 shadow promotions with acceptable quality, real promotion begins. This is standard practice for any automated system that writes prod — the spec skips it entirely.

---

### B. Score staleness — sources degrade over time 🟡 MEDIUM

A source is scored at acquisition. The score lives in `sources.score`. No re-score trigger exists if the source degrades: website goes behind paywall, government portal restructures, the source becomes a redirect to a generic page.

Facts promoted under a now-degraded source retain their high score. The currency layer will fetch the source again — but if the source is unreachable, what happens? The spec says "weekly targeted search on past-backstop items" but says nothing about source validation failures triggering automatic 🟡 routing regardless of current score.

---

### C. `corrob ≥ 10` threshold blocks legitimate facts 🟡 MEDIUM

10 corroborating sources for AUTO promotion is a high bar. For obscure freezone fees or niche treaty provisions, you will not find 10 corroborating sources. These facts permanently route to 🟡 regardless of their individual source quality.

This creates a systematic bias: well-known facts (UK/France) auto-promote. Obscure but correct facts (small-GCC-freezone fee schedule) never auto-promote. The spec frames this as a quality gate but it's actually a coverage bias.

---

### D. GDrive atomic write — orphan fact risk 🔴 CRITICAL (red-line #4 violation)

The executor pseudocode writes the DB UPSERT and the GDrive promotion bundle in sequence. They are not atomic. If GDrive is unavailable:
- Option A: DB write first → GDrive write fails → fact in prod, no evidence link → **red-line #4 violated**
- Option B: GDrive write first → succeeds → DB write fails → fact not in prod, evidence link orphaned in GDrive → confusing but not a red-line violation

The fix is option B with a retry queue: write GDrive first, capture the link, then write DB with the link embedded. If DB write fails, retry with the already-written GDrive link. No orphan facts can occur in this ordering.

The spec must define this write ordering explicitly, or Talos will implement option A (the natural order: write your main store first, then your audit log).

---

### E. Notification gap — silent auto-promotions 🟡 MEDIUM

The spec goal is "nothing rots silently." But there's an equal risk: things promote silently. A fact can enter prod without anyone being notified. If the promoted value is wrong (passed all gates due to coordinated failure), no one knows until a client or internal user notices.

**Minimum viable:** daily digest of all auto-promotions in the last 24h, sent to Peter and Varys. Not for approval — for awareness. This costs nothing and catches runaway-executor bugs within 24h instead of weeks.

---

### F. "Not-in-force" flag — undefined lifecycle 🟠 HIGH

The spec mentions a `not-in-force` flag as a condition that prevents AUTO promotion. It does not define:
- Who sets this flag (Varys during acquisition? automated status detection?)
- How it's cleared when a treaty enters force
- What happens to a fact that was promoted before the flag was set (treaty signed but not ratified yet)

A treaty being "signed" vs "in force" is a legally material distinction. Getting this wrong — auto-promoting a signed-but-not-in-force treaty — is the kind of error the spec was designed to prevent. The flag's lifecycle must be specified, not left to implementation.

---

## Summary Scorecard

| Area | Rating | Core Risk |
|---|---|---|
| Varys self-scoring | 🟠 HIGH | Acquirer bias corrupts panel independence |
| Vulcan double-role | 🟠 HIGH | Same blind spot in both gates |
| Median robustness | 🟡 MEDIUM | Dissent overruled within spread threshold |
| Executor blast radius | 🔴 CRITICAL | No dry-run, no row cap, no rollback, GDrive/DB not atomic |
| Changed-value detection | 🟠 HIGH | Natural key undefined; hash collision risks |
| Alert noise | 🟡 MEDIUM | Daily triage is not "~free" at scale |
| Backstop starvation | 🟠 HIGH | Cadences defined but not enforced |
| Idempotency under race | 🟠 HIGH | Parallel executors can create ghost versions |
| No burn-in period | 🔴 CRITICAL | Full auto-write with no graduated trust |
| GDrive/DB write order | 🔴 CRITICAL | Red-line #4 violable on GDrive failure |
| Not-in-force flag | 🟠 HIGH | Lifecycle undefined |

**3 CRITICAL issues must be resolved before Talos builds the executor.** The executor is the blast radius. Get that design right first — everything else can be fixed after.

---

*Vulcan — adversarial review complete. Not a checklist. These are the ways this breaks in prod.*
