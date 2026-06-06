VERDICT: CONDITIONAL

## Filesystem Claim Verification

### 1. Plan file exists
- `/opt/gcg/shared/plans/provenance-queue-master-plan-2026-06-06.md` → **EXISTS** ✓

### 2. SOURCE_DATABASE.md
- **Claim:** Referenced as if in shared docs
- **Reality:** File exists at `/opt/gcg/openclaw-varys/workspace/SOURCE_DATABASE.md` — NOT in `/opt/gcg/shared/docs/`
- **Severity:** MEDIUM — path differs, but the file exists

### 3. `sources` table columns — CRITICAL DISCREPANCIES

**Plan claims (RED LINE #4 paragraph):**
> `sources` already carries `subject_type`/`subject_id` (entity link), `cadence_days` (set on all 85), `last_fetched_at` (only 5 populated — gap), `source_health`, `score`, `is_rejected`, `authority_tier`

**Actual schema** (auto-generated ANNOTATED_SCHEMA.md 2026-06-06T04:21:45+00:00):

| Column         | Exists in schema? | Notes |
|----------------|-------------------|-------|
| subject_type   | YES ✓             | Column 2 |
| subject_id     | YES ✓             | Column 3 |
| cadence_days   | YES ✓             | Column 6, NOT NULL DEFAULT 30 |
| last_fetched_at| YES ✓             | Column 7, NULL allowed |
| authority_tier | YES ✓             | Column 5 |
| **source_health** | **PARTIAL**   | NOT in ANNOTATED_SCHEMA (generated before migration), but added by `003_autopromote_infra.sql` (header says "Applied: 2026-06-06") |
| **score**      | **PARTIAL**       | NOT in ANNOTATED_SCHEMA. The `003_autopromote_infra.sql` migration header says "score/scored_by already exist" — meaning they existed in DB before this migration. Column exists in DB but not in the auto-generated schema doc. |
| **is_rejected**| **PARTIAL**       | NOT in ANNOTATED_SCHEMA. No migration file was ever written (005_is_rejected_column.sql does NOT exist). PRIOR review (search-sharpening-R2-CONFUCIUS.md) confirmed the column EXISTS in DB as `BOOLEAN NOT NULL DEFAULT false` but was added via manual ALTER with no migration artifact. |

**Summary:** The plan overstates column completeness. Three columns (`source_health`, `score`, `is_rejected`) exist in the live DB but have NO migration documentation. The plan presents them as if they're fully settled when `source_health` and `is_rejected` were added via undocumented manual ALTERs. Blocking if migration history matters; non-blocking if the team accepts manual schema drift.

### 4. `freshness_scan_log`
- **Claim:** "already exists but is unused"
- **Reality:** **EXISTS** ✓ — defined in ANNOTATED_SCHEMA with 7 columns (scan_id, scan_date, source_snapshot_hash, superseded_count, stale_count, status, notes)
- Also `freshness_maintenance.py` at `/opt/gcg/shared/gcg_tools/freshness_maintenance.py` manages this table with CREATE TABLE IF NOT EXISTS
- **Verdict:** TRUE — table exists and is unused (freshness_maintenance accesses it but is a cron that hasn't been run to populate data)

### 5. Queue infrastructure
| Queue              | Exists? | Evidence |
|--------------------|---------|----------|
| dispatch_queue     | YES ✓   | In ANNOTATED_SCHEMA |
| follow_up_queue    | YES ✓   | In ANNOTATED_SCHEMA |
| peter_tap_queue    | YES ✓   | Created by `003_autopromote_infra.sql` migration, NOT in ANNOTATED_SCHEMA (pre-generated) |
| lightrag_ingest_queue | YES ✓ | In ANNOTATED_SCHEMA |

**Verdict:** TRUE — all four queues exist

### 6. "3 scored (UK/RU/FR pilot)"
- **Reality:** SOURCE_DATABASE.md at `/opt/gcg/openclaw-varys/workspace/SOURCE_DATABASE.md` contains a "DTA RATES — Phase 3.1" section dated 2026-06-06 that lists UK (dta_id=133), Russia (dta_id=104), and France (dta_id=45) with extracted WHT rates and source tiers
- **UK:** T0 gov.uk PDF, rates extracted ✓
- **Russia:** T0 mof.gov.ae PDF, rates extracted, ⚠️ NOT IN FORCE
- **France:** T3 unofficial translation, rates extracted, ⚠️ T0 Arabic needed
- **Verdict:** TRUE — evidence of these 3 DTAs being worked exists

### 7. Autopromote infrastructure
- `003_autopromote_infra.sql` migration exists at `/opt/gcg/shared/gcg_tools/migrations/003_autopromote_infra.sql` and creates:
  - `agents` table
  - Adds `source_health`, `acquired_by` to sources
  - `staging_promoted`
  - `peter_tap_queue`
  - `promotion_audit_log`
  - `executor_circuit_breaker`
- **Verdict:** The claimed autopromote bands match the spec ✓

### 8. Entity counts for the provenance view
| Domain | Plan claims | Can verify? |
|--------|------------|-------------|
| Freezones | 56 | Table exists (`freezones`), exact count needs DB |
| DTAs | 152 | Schema says 142, plan says 152 — DISCREPANCY |
| Legislation | 718 | No `legislation` table in ANNOTATED_SCHEMA — UNVERIFIABLE |
| Gov fees | 45 | Gov fees table? Not immediately identifiable in schema — PARTIAL |

**Finding:** The plan says 152 DTAs but the ANNOTATED_SCHEMA comment says "142 treaties" on `dta_agreements`. This is a HIGH discrepancy (10 DTAs off).

---

## CRITICAL FINDINGS

1. **`is_rejected` column on `sources` has NO migration file.** It exists in the DB (confirmed by prior review, search_precheck.py references it, code inserts/selects it) but no ALTER TABLE migration was ever written. The plan presents this as settled infrastructure. The prior review cycle flagged this as R2-8 ("Create migration SQL for is_rejected column") but it was never closed out.

2. **DTA count mismatch: 142 vs 152.** The `dta_agreements` table comment says "142 treaties" but the plan says "152 DTAs". A 10-treaty gap affects P4 burn-down planning.

## HIGH FINDINGS

3. **`source_health` column was recently added** by `003_autopromote_infra.sql` migration (Applied: 2026-06-06) but the ANNOTATED_SCHEMA (generated 04:21 same day) doesn't reflect it. If the migration ran at or after that time, the plan's claim is now accurate but would be confusing to anyone reading the schema doc.

4. **`source_health` and `score` columns are used by `search_precheck.py`** which assumes they exist. If these columns don't actually exist in all environments, that code will crash at runtime.

5. **`peter_tap_queue`** was created by the autopromote migration but is NOT in the ANNOTATED_SCHEMA doc. The plan claims it as existing infra — the migration was written and available at plan write time, so the claim is correct in context, but the schema doc is stale.

## MEDIUM FINDINGS

6. **SOURCE_DATABASE.md** is not in `/opt/gcg/shared/docs/` as the plan implicitly suggests — it's at `/opt/gcg/openclaw-varys/workspace/SOURCE_DATABASE.md`. Not a blocking issue but a path consistency concern.

7. **No `legislation` table** visible in ANNOTATED_SCHEMA. The plan claims 718 laws. If a table exists with another name, the plan should reference it. If it doesn't exist at all, the P4 burn-down count of 718 is invented.

## VERIFIED

- Plan file exists ✓
- `sources` table exists with `subject_type`, `subject_id`, `cadence_days`, `last_fetched_at`, `authority_tier` ✓
- `freshness_scan_log` exists and is unused ✓
- All 4 queue tables exist ✓
- Autopromote migration (003) exists with all claimed artifacts ✓
- UK/RU/FR DTA pilot evidence exists in SOURCE_DATABASE.md ✓
- `dta_agreements` table exists with FK to sources ✓

## NOTES

- The plan was written with awareness of pending DB changes (migration 003 was in flight). Several reality gaps are timing-related rather than genuinely wrong.
- The prior Confucius review of this same plan (on gcg_intelligence DB) gave FAIL because the `sources` table didn't exist in that database. The current review is against the gcg (public schema) database where the table does exist.
- **Legislation (718 laws):** Need to verify what table holds legislation data before this plan can execute P4.
