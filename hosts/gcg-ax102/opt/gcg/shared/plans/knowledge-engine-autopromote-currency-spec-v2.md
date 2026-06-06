# Knowledge Engine — Autopromote + Currency Build Spec v2

> Owner: Varys | 2026-06-06 | Status: post-red-team, Peter decisions locked → for Daen QA → Peter sign-off → Talos build
> Supersedes v1. Folds in all 3 red-team critiques (Daen/Talos/Vulcan) + Peter's 4 decisions (#4279).
> Authorizes the FIRST automated staging→prod write path in GCG. Read the red-lines before building.

## What changed from v1 (the deltas red-team forced)
| # | v1 | v2 (this spec) | Source |
|---|---|---|---|
| D1 | AUTO = median ≥90, spread ≤15 | **AUTO = ALL gate-scorers ≥90** (unanimous band) | Peter #4279 / Vulcan Q3 |
| D2 | corrob ≥10 flat | **≥10 for DTA/tax-law · ≥2-3 for freezone fees** | Peter #4279 / Vulcan C |
| D3 | 🟡 queue undefined TTL | **re-prompt daily @72h · Telegram-escalate @7d · NEVER auto-promote** | Peter #4279 / Daen 9c, Talos 10 |
| D4 | Peter one-time sign-off then live | **30 shadow promotions reviewed BEFORE live writes** | Peter #4279 / Vulcan A |
| B1 | executor writes prod directly | **dry-run/shadow → batch cap ≤50 → rollback() → circuit breaker → GDrive-link-FIRST then DB commit** | all 3 🔴 |
| B2 | Varys on the scoring panel | **acquirer score EXCLUDED from gate (advisory-logged); gate = non-acquirer scorers** | all 3 🔴/🟠 |
| B3 | "idempotent at content_hash" | **pg_advisory_xact_lock(natural_key); single work-queue, no parallel triggers** | Talos 🔴 / Vulcan 🟠 |
| H1 | Vulcan scores AND QAs | **value QA = deterministic script, no access to scores, fresh source fetch** | all 3 |
| H2 | natural key implicit | **defined per fact table (below)** | all 3 |
| H3 | backstop = "weekly search" | **non-deferrable cron; hard escalate at 1.5× cadence** | all 3 |
| H4 | daily = "~free" | **auto-dismiss heuristics + Varys triage gate; Peter never sees raw alerts** | all 3 |

---

## Part A — Scoring panel (3 infra agents, acquirer recused)
- Scorers: Varys, Talos, Vulcan — each scores the SOURCE on the 100-pt rubric (`source-scoring-rubric-v1.md`) alone, no collusion.
- **Acquirer recusal (B2):** the agent that acquired the source has its score logged as **advisory only, excluded from the gate**. Varys acquires almost everything → in practice the gate panel is **Talos + Vulcan**. The acquirer's advisory score is shown in the bundle so Peter can spot inflation patterns.
- `source.acquired_by` column records who sourced it (drives the recusal).
- **Score freshness (Talos #12):** the gate reads scores computed within a 24h window. Any score >24h old at promotion time is re-requested. Median/band computed AT promotion time, not collection time.
- **Coordinator + timeout (Daen 9b):** a new source enqueues a scoring task to each scorer. When all non-acquirer scorers return → band. If a scorer is silent >24h (bulk) / >1h (urgent), it drops out (logged), gate falls to remaining scorers; if <2 gate-scorers remain → route 🟡.

## Part B — Bands (Peter-locked #4279)
| Band | Rule | Action |
|---|---|---|
| 🟢 AUTO | **ALL gate-scorers ≥90** · tier ≤T1 · corrob ≥ threshold(D2) · QA=GREEN · no flag | executor writes prod (after burn-in) |
| 🟡 PETER | any gate-scorer 70–89, OR all ≥90 but carrying a flag | one-tap queue; TTL per D3; **never auto-promotes** |
| 🟠 HOLD | any gate-scorer 40–69 (and none <40) | back to Varys, upgrade source |
| 🔴 REJECT | any gate-scorer <40, or tier T3 | drop, re-source |

**corrob threshold (D2):** DTA / tax-law / CFC / exit-tax → ≥10. Freezone fees/packages → ≥2 (≥3 preferred). The threshold travels with the fact's data_type.

## Part C — Value QA gate (deterministic, decoupled — H1)
- **Not an agent scoring judgment.** A deterministic script (Talos builds) that compares the staged structured value against the source document text via a **fresh fetch** (never a cached read from the scoring pass).
- No access to the source scores → eliminates the anchoring coupling.
- Output per row: GREEN / RED + reason. RED blocks promotion in every band.
- If a row genuinely needs interpretation (ambiguous clause), it escalates to an agent who did NOT score that row → manual 🟡, never silent GREEN.
- **Normalization (Daen 6 / Talos 5):** values normalized to canonical form (percentage/currency/date/text rules, shared util) BEFORE comparison and BEFORE hashing — so "15%" == "15.0%" and formatting noise doesn't false-trigger.

## Part D — Autopromote executor (the prod-writer) — Talos builds
Single function, the ONLY component permitted to write prod tables. Hardened per B1/B3:
```
promote(staging_row):
  lock = pg_advisory_xact_lock(hash(row.natural_key))   # B3: serialize per entity
  if already_promoted(row.content_hash): return NOOP      # idempotent
  scores  = gate_scores(row.source)        # non-acquirer scorers only (B2)
  qa      = value_qa(row)                  # deterministic script, GREEN/RED (Part C)
  flags   = collect_flags(row)             # contradiction / not-in-force / changed-value / source_unreachable
  if any(s.tier == T3) or any(s < 40):           return REJECT
  if any(40 <= s <= 69):                         return HOLD
  if all(s >= 90) and tier <= T1 and corrob >= threshold(row.data_type)
        and qa == GREEN and not flags:
        # ORDER MATTERS (Vulcan D / Talos 11): evidence FIRST, then prod
        gdrive_link = write_promotion_bundle()        # record+scorecard+snapshot
        BEGIN TXN:
          UPSERT staging->prod by natural_key WITH gdrive_link embedded
          stamp last_verified=now(); set valid_from; close prior version valid_to
          write audit row
        COMMIT
        if DRY_RUN or SHADOW: rollback TXN, log "would promote" to shadow table instead
        return PROMOTED
  else: enqueue_peter_tap(row); return PENDING_PETER
```
**Safeguards (B1, non-negotiable before any live write):**
1. **Burn-in (D4):** `SHADOW` mode for the first **30 promotions** — writes to `staging_promoted` shadow table + alerts Peter "would have promoted X (before/after diff)". Live writes enable only after Peter reviews those 30 and removes the flag.
2. **Batch cap:** hard max 50 rows / invocation. Exceeding → fail + alert, no writes.
3. **Circuit breaker:** 3 consecutive validation failures → halt executor, alert Daen+Peter, manual reset only.
4. **rollback(promotion_id):** automated revert — re-open prior version `valid_to`, close current `valid_from`. Must exist + be tested before first live promote. Plus time-bounded "roll back last hour".
5. **GDrive-FIRST ordering:** evidence bundle written and link captured BEFORE the DB commit. GDrive failure → no DB write → no orphan fact (red-line #4). If DB write fails, retry reuses the already-written link.
6. **Heartbeat:** alert if items are queued but no promotion has run in >24h (silent-crash guard).
7. **Daily digest (Vulcan E):** every auto-promotion in the last 24h summarised to Peter+Varys — awareness, not approval; catches a runaway executor within a day.

## Part E — Data-model changes (Talos) — the zero-prod-risk parts, build NOW
1. `staging_dta_agreements` += `source_file_path`, `source_folder_url`, `source_accessed_at` (mirror staging_packages). Backfill existing rows' Drive link → "DTA Source Documents — 2026-06-02" (`1cinOSHbZ-mwCO0C2E8KcC2ge63LbY26h`).
2. `sources` += `score`, `score_detail` (jsonb, per-scorer), `scored_at`, `scored_by`, `acquired_by` (B2), `source_health` (HEALTHY/STALE/DEAD — Daen 9a/Vulcan B). Grant Varys INSERT/UPDATE.
3. Versioning on prod fact tables: `valid_from`, `valid_to`, `last_verified`, `content_hash`, `normalized_value`, `primary_source_id` FK + `corroborating_source_ids[]` (Daen 9d). Never overwrite — supersede.
4. Peter-tap queue: table/view the superapp renders (row + scorecard + value + band + age). Drives D3 TTL.
5. Unique constraint `(natural_key, content_hash) WHERE valid_to IS NULL` (B3 belt-and-suspenders).
6. `work_spine`: grant SELECT/INSERT/UPDATE to other agent roles (fleet-wide; doc `/opt/gcg/shared/docs/WORK_SPINE.md`).

### Natural keys (H2 — verified against prod schema 2026-06-06; LOCKED option (a) by Peter #4297)

**Decision (Peter #4297):** keep the existing flat country-level model — option (a). A DTA's 3 rates all come from one treaty = one source = one row; per-rate normalization (option b) would duplicate source_id across rows for no provenance gain and remodel a live prod table. NO dta_rates migration. Changed-value guard operates on the country row's content_hash.

- **DTA fact:** `(country_code)` — prod `dta_agreements`, one row per country with dividend/interest/royalty as separate columns (dividend_rate_pct, interest_rate_pct, royalty_rate_pct). `effective_date` is an attribute, not key. **LOCKED — no normalization migration.**
- **Freezone package:** `(freezone_id, package_code)` — matches existing UK on `freezone_packages`.
- **Freezone fee:** `(freezone_id, category, item_name)` — matches columns on `freezone_fee_schedule` (no license_type/office_type columns there; those exist on `freezone_packages`). No UK exists yet — **migration needed: ADD UNIQUE (freezone_id, category, item_name).**
- **Tax-law fact:** `(rule_code)` — matches existing UK on `tax_rules`. No jurisdiction/tax_year columns exist on this table (they're on staging_legislation). **If jurisdiction-level key is needed → specify target table or migration.**

## Part F — Currency layer (refresh) — built behind A–E, reuses the engine
**Event-driven, not time-driven.** Three lanes, all → `work_spine` finding → re-score → value QA → re-band:
- **Daily (no search):** Google Alerts + monitored inbox. **Varys triage gate (H4):** auto-dismiss non-gov domains / dupes-within-7d / non-fact diffs; only signals passing triage create a finding. Peter never sees raw alerts.
- **Weekly (targeted):** deep search ONLY on flagged / pending-status / past-backstop items.
- **Backstop cron (H3, non-deferrable):** separate cron, NOT the currency layer. `WHERE last_verified < now() - cadence_days`. Cadences: in-force DTA 365 · pending DTA 7 · tax law/CFC/exit 90 · freezone fees 30–60. Any item past **1.5× cadence** → hard escalate to Peter+Varys daily until resolved. Overdue items get forced priority in the weekly run.
- **Changed-value guard:** a changed *normalized* value on an in-force fact forces 🟡 even at all-≥90. Same-value refresh auto-promotes (re-stamps last_verified). Detection = compare incoming normalized_value/content_hash to the CURRENT prod row for that natural_key.
- **not-in-force lifecycle (Vulcan F):** `treaty_status` set at acquisition; a signed-not-in-force fact can never AUTO (hard flag); cleared only when ratification confirmed → re-flows → 🟡 for Peter.
- **source_health:** backstop fetch failure → mark STALE/DEAD, force 🟡, alert. Degraded source never silently retains its old high score.
- **Superapp:** score + band + last_verified + freshness dot + source_health per row.

---

## 🔴 Red-lines (do NOT design around these)
1. The **executor is the only prod-writer**; ships only after Daen QA + Peter's one-time enablement sign-off + the 30-promotion burn-in (D4). "One-time sign-off" = initial enablement authorization, NOT per-batch (clarifies Daen 9e). Until enabled, everything stays in staging / shadow.
2. No fact auto-promotes with value QA = RED, regardless of score.
3. Rates/values never default to 0; NULL = unknown.
4. Every promoted row carries primary_source_id + content_hash + Drive evidence link, written BEFORE the prod commit. No orphan facts.
5. Versioned supersede, never destructive overwrite.

## Open items for Daen QA
- Natural-key definitions (Part E) now verified against prod schema + migrations listed. Confirm or revise.
- Confirm the deterministic value-QA script is feasible for treaty PDFs (text extraction reliability) — if not, define the interpret-escalation path concretely.
- Confirm 2-gate-scorer minimum (Talos+Vulcan when Varys acquires) is acceptable vs pulling a rotating Council 3rd; flag if a 2-panel weakens the gate too much.
- Sign off or send back with required changes.

## Item 2 resolution — Treaty PDF QA two-tier path (per Varys review 2026-06-06)

The deterministic value-QA for treaty PDFs operates at two tiers:

### (a) Structured-promote path (automated)
For PDFs with extractable structured data (tables with clear rate columns):
- Script extracts the treaty rate table via text parsing
- Normalizes values ([canonical form](/opt/gcg/shared/normalization-rules.md))
- Compares staged values against extracted text
- Output: GREEN (match) / RED (mismatch) per row
- Triggers: GREEN rows proceed through the scoring band gate normally

### (b) Interpret-escalation path (yellow manual lane)
When the QA script's confidence is <70% (ambiguous clause wording, nested conditions, scanned-table OCR noise):
- QA outputs YELLOW + confidence score + ambiguity reason
- Route to the yellow manual lane: Varys reviews the source PDF directly, resolves ambiguity, updates staging, re-triggers QA
- **Never silently GREEN** a below-70-confidence row
- The confidence score is a mandatory output of the QA script (not optional)
- Varys accepts this role as yellow-lane treaty PDF reviewer (confirmed)
