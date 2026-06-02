# Fix Registry Changelog

## 2026-06-02 — v1.1.0 — INDEX.md + hooks + search

- Added INDEX.md — searchable table of 15 fix entries ✅
- Added post-commit git hook — auto-regenerates INDEX.md on every commit to fixes/ ✅
- Added Phase 2: auto-extraction hook at `/opt/gcg/shared/hooks/fix-extraction/extract_fix.py` ✅
  - Triggers on `session:compact:after` for infra agents
  - Fix detection heuristics with minimum 2 indicators
  - Dedup via ID check before writing
  - Auto-commits to git after extraction
- Added Phase 4: pgvector search at `/opt/gcg/shared/hooks/fix-extraction/search_fixes.py` ✅
  - `fixes.registry` table in PostgreSQL
  - Full-text ILIKE search across symptom, root_cause, solution
  - `--index` flag to batch-index all fix entries
- ROLLBACK.md with revert procedures ✅

## 2026-06-02 — v1.0.0 — Registry created

- Created `fixes/` directory in gcg-infra
- Established `fix-schema.yaml` v1.0.0
- Created TEMPLATE.md for manual entries
- EVENT-VERIFIED.md — session:compact:after confirmed as trigger
- Phase 1 complete ✅
- Phase 3 complete ✅ (15 entries migrated: 14 existing + 1 new)
- Migration tool: migrate_existing_fixes.py
