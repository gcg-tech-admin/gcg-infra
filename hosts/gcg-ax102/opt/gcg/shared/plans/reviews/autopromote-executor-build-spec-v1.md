# Autopromote Executor — Build Spec v1

**Owner:** Talos | **Date:** 2026-06-06 | **Status:** DRAFT (pending Daen QA → Peter sign-off)
**Authority:** Peter decision 2026-06-06 — banded autopromote for source-scored records (x=90, y=70)

---

## 1. Overview

A mechanism that takes a staged intelligence record (agreement/package/fee), its median source score, Vulcan QA status, and flags, and automatically routes it to the correct production lane.

No human touching 🟢-band records with clean flags. Peter one-taps 🟡. Varys reworks 🟠. 🔴 gets rejected.

---

## 2. Input Contract

The executor receives a bundle:

```python
{
  "record": {
    "id": str,
    "type": "dta_agreement" | "package" | "fee",
    "staging_table": str,          # e.g. "staging_dta_agreements"
    "staging_id": int,
    "data": dict,                  # the staged value(s)
  },
  "scoring": {
    "median": int,                 # median of 3 panelist totals (0-100)
    "spread": int,                 # max - min across panelists
    "tier": "T0" | "T1" | "T2" | "T3",
    "corroboration": int,          # sub-score (0-15)
    "source_url": str,
    "source_folder_url": str | None,
    "source_accessed_at": datetime,
    # per-panelist breakdown stored in registry table
  },
  "qa": {
    "status": "green" | "yellow" | "red",
    "checked_by": "vulcan",
    "checked_at": datetime,
    "notes": str | None,
  },
  "flags": [
    # rule: if ANY flag is contradiction on terms (not rates), forces yellow lane
    "contradiction" | "spread_high" | "tier_mismatch" | "qa_note" | str
  ],
  "staged_by": str,                # agent name
  "staged_at": datetime,
}
```

---

## 3. Band Logic

```
                     ┌─────────────────────────────┐
                     │  Input: record + score + QA  │
                     └─────────────┬───────────────┘
                                   │
                     ┌─────────────▼───────────────┐
                     │  median < 40 OR tier == T3   │──→ 🔴 REJECT
                     └─────────────┬───────────────┘
                                   │ no
                     ┌─────────────▼───────────────┐
                     │  median 40-69                 │──→ 🟠 HOLD (back to Varys)
                     └─────────────┬───────────────┘
                                   │ no
                     ┌─────────────▼───────────────┐
                     │  median 70-89                │──→ 🟡 PETER GATES
                     └─────────────┬───────────────┘
                                   │ no (median >= 90)
                     ┌─────────────▼───────────────┐
                     │  Check all: tier ≤ T1        │
                     │  corroboration ≥ 10          │
                     │  spread ≤ 15                 │
                     │  QA status == green          │
                     │  no contradiction flag       │
                     └─────────────┬───────────────┘
                          all yes│        │any no
                         ┌────────▼──┐ ┌──▼────────┐
                         │ 🟢 AUTO   │ │ 🟡 PETER  │
                         │  PROMOTE  │ │  GATES    │
                         └───────────┘ └───────────┘
```

### Hard guards (override everything)
1. **Any contradiction flag** (a source disagrees with a T0/T1 on *terms*, not just rates) → forces 🟡 regardless of score.
2. **Vulcan QA must be green** even for auto-promote — confirms the staged value matches the source value.
3. **If spread > 15**, score >= 90 still routes to 🟡 (Peter-gate lane) because median alone may not be reliable.

---

## 4. 🟢 Auto-Promote Action

When a record clears all gates:

```
1. EXECUTE:  COPY staging_<type> → prod_<type>
   - SQL: BEGIN; INSERT INTO prod_<type> (...) SELECT ... FROM staging_<type> WHERE id = <staging_id>; DELETE FROM staging_<type> WHERE id = <staging_id>; COMMIT;
   - Or: UPDATE staging_<type> SET status = 'promoted' WHERE id = <staging_id>

2. WRITE:    Promotion bundle to GDrive 'promoted' folder
   - Bundle = JSON containing:
     - promoted_record (the data)
     - scorecard (scoring.median, tier, corroboration, spread, panel URLs)
     - snapshots: staging record snapshot, source URL, QA report
     - promoted_at timestamp
   - File naming: {type}_{staging_id}_{timestamp}.promotion.json

3. STAMP:    Update record metadata
   - Set last_verified = NOW()
   - Set promoted_by = 'autopromote-executor-v1'
   - Write source_file_path, source_folder_url, source_accessed_at on the prod record
```

### GDrive integration
- Uses broker_client_v2 (Google Drive API) with Peter impersonation
- Folder: `/GCG Intelligence/Promoted/`
- File structure per promotion:
  ```
  promoted/
  ├── dta_agreements/
  │   └── 2026-06-06_uk_dta_001.json
  ├── packages/
  └── fees/
  ```

---

## 5. 🟡 Peter Gates Action (70-89 OR >=90 with flag)

```
1. QUEUE:   Write to Peter's one-tap approval queue (DB table: pending_approvals)
   - record_id, source, score, reason_for_gate, snapshot_link
   - status = 'pending'
   - created_at = NOW()

2. NOTIFY:  Fleet-wake Peter with summary
   - "New record needs approval: {type}/{id} — score {median} — reason: {flag or band}"
   - Include GDrive snapshot link

3. ON APPROVE:
   - Execute same promote actions as 🟢
   - Log approved_by = 'peter'
   - Timestamp approved_at

4. ON DENY:
   - Log with reason
   - Route to 🟠 HOLD (back to Varys)
```

---

## 6. 🟠 HOLD Action (40-69)

```
1. QUEUE:   Write to Varys rework queue
   - record_id, source, score, panelist verdicts, reason
   - status = 'rework_needed'

2. NOTIFY:  Fleet-wake Varys with summary
   - "Record {type}/{id} scored {median} — needs upgraded source (T0/T1) and re-score"
```

---

## 7. 🔴 REJECT Action (<40 OR T3)

```
1. QUEUE:   Write to rejected registry
   - record_id, source, score, reason = 'below_minimum|tier_three_source'
   - status = 'rejected'
   - rejected_at = NOW()

2. NOTIFY:  Fleet-wake Varys
   - "Record {type}/{id} rejected — source too weak for staging. Re-source required."
```

---

## 8. DB Schema Changes

### Staging tables need 3 new evidence columns

```sql
-- Add to staging_dta_agreements (mirrors staging_packages pattern)
ALTER TABLE staging_dta_agreements ADD COLUMN IF NOT EXISTS source_file_path TEXT;
ALTER TABLE staging_dta_agreements ADD COLUMN IF NOT EXISTS source_folder_url TEXT;
ALTER TABLE staging_dta_agreements ADD COLUMN IF NOT EXISTS source_accessed_at TIMESTAMPTZ;
```

### New table: pending_approvals
```sql
CREATE TABLE IF NOT EXISTS pending_approvals (
  id SERIAL PRIMARY KEY,
  record_type VARCHAR(50) NOT NULL,
  record_id INTEGER NOT NULL,
  staging_table VARCHAR(100),
  median_score INTEGER NOT NULL,
  reason_for_gate TEXT NOT NULL,
  snapshot_link TEXT,
  scorecard_json JSONB,
  status VARCHAR(20) DEFAULT 'pending',  -- pending | approved | denied | rework
  created_at TIMESTAMPTZ DEFAULT NOW(),
  approved_at TIMESTAMPTZ,
  denied_at TIMESTAMPTZ,
  denied_reason TEXT,
  approved_by VARCHAR(50)
);
```

### New table: promotion_log
```sql
CREATE TABLE IF NOT EXISTS promotion_log (
  id SERIAL PRIMARY KEY,
  record_type VARCHAR(50) NOT NULL,
  record_id INTEGER NOT NULL,
  source_url TEXT NOT NULL,
  median_score INTEGER NOT NULL,
  band VARCHAR(10) NOT NULL,  -- green | yellow | orange | red
  promoted BOOLEAN DEFAULT FALSE,
  gdrive_link TEXT,
  promoted_by VARCHAR(50) DEFAULT 'autopromote-executor-v1',
  promoted_at TIMESTAMPTZ DEFAULT NOW(),
  scorecard_json JSONB,
  qa_status VARCHAR(10),
  flags TEXT[]
);
```

---

## 9. Architecture

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│  Staging DB │────▶│   Executor   │────▶│  Prod DB     │
│  (AX42)     │     │   (Talos)    │     │  (AX42)      │
└─────────────┘     └──────┬───────┘     └──────────────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │ GDrive   │ │ Fleet   │ │ Peter    │
        │ Promoted │ │ Notify  │ │ Approval │
        │ Folder   │ │ Varys   │ │ Queue    │
        └──────────┘ └──────────┘ └──────────┘
```

- **Runtime:** Cron-based (every 15 min) + event-triggered (on staging insert)
- **Agent:** Talos executes the autopromote logic (Peter-authorized)
- **DB:** All DB operations via shared db_config (gcg_intelligence on AX42)

---

## 10. Error Handling

| Failure | Action |
|---------|--------|
| DB connection lost | Retry 3x (exponential backoff), then fleet-warn Daen |
| GDrive write fails | Log locally, retry next cycle. Do NOT block promotion |
| Partial promotion (DB committed but GDrive failed) | Idempotent — next cycle detects already-promoted, writes GDrive only |
| QA data missing | Block — do not promote. Escalate to Vulcan |
| Concurrent promotion on same record | `SELECT FOR UPDATE` lock on staging row |

---

## 11. Implementation Order

1. Add evidence columns to `staging_dta_agreements` (8-column ALTER TABLE)
2. Create `pending_approvals` and `promotion_log` tables
3. Implement band logic as a Python script (`/opt/gcg/shared/gcg_tools/autopromote.py`)
4. Implement GDrive promotion bundle writer (`/opt/gcg/shared/gcg_tools/autopromote_drive.py`)
5. Wire fleet notification helpers
6. Create cron job (every 15 min): `python3 -m gcg_tools.autopromote poll`
7. Create staging insert trigger (event-based)
8. Daen QA → Peter sign-off → enable

---

*Drafted by Talos 2026-06-06. Ready for Daen QA review.*
