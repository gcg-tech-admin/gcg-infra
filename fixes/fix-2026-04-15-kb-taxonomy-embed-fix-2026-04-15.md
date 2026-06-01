---
id: fix-2026-04-15-kb-taxonomy-embed-fix-2026-04-15
date: '2026-04-15T00:00:00Z'
agent: mnemosyne
severity: warn
status: resolved
systems:
- agent-fleet
- embedding-pipeline
- security
tags:
- agent-fleet
- embedding
- security
symptom: 'Fixed KB taxonomy violations and embedding pipeline failures triggered by hourly alerts since midnight. Moved 24
  misfiled documents from `/opt/gcg/shared/docs/` root to correct subdirectories (reference/audits/, reference/cascade-artifacts/,
  reference/, architecture/); resolved 313 stale backlog entries. Re-embedded 3 files via talos agent, writing 91 chunks to
  `agent_memory` table. `knowledge_embeddings` skipped (frozen at 933,515 rows by design). Full report: `/opt/gcg/shared/reports/kb-fix-2026-04-15.md`.'
root_cause: ''
solution: 'KB taxonomy violations and embedding pipeline failures triggered by hourly alerts since midnight. Moved 24 misfiled
  documents from `/opt/gcg/shared/docs/` root to correct subdirectories (reference/audits/, reference/cascade-artifacts/,
  reference/, architecture/); resolved 313 stale backlog entries. Re-embedded 3 files via talos agent, writing 91 chunks to
  `agent_memory` table. `knowledge_embeddings` skipped (frozen at 933,515 rows by design). Full report: `/opt/gcg/shared/reports/kb-fix-2026-04-15.md`.'
files_changed:
- path: /opt/gcg/shared/docs/
  action: modified
- path: /opt/gcg/shared/reports/kb-fix-2026-04-15.md
  action: modified
detection_method: Root-level doc dumps break knowledge maintenance alerts and embed reconciliation workflows.
detection_agent: mnemosyne
resolution_time_minutes: null
related_fixes: []
lessons_learned: ''
prevention: Verify `knowledge_maintenance.py` runs on schedule (hourly) and alerts stop on next cycle. Docs root must stay
  clean; taxonomy enforced via script.
extensions:
  source_file: kb_taxonomy_embed_fix_2026_04_15.md
  source_title: Kb Taxonomy Embed Fix 2026 04 15
source_session: 72286a3e-908f-4bc5-8145-46766c588b47
---
