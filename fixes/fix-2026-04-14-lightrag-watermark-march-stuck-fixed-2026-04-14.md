---
id: fix-2026-04-14-lightrag-watermark-march-stuck-fixed-2026-04-14
date: '2026-04-14T00:00:00Z'
agent: mnemosyne
severity: warn
status: resolved
systems:
- agent-fleet
- embedding-pipeline
tags:
- agent-fleet
- embedding
symptom: Fixed LightRAG incremental pipeline watermark stuck at 2026-03-04 06:17:44 in knowledge_embeddings processing (1.37M
  rows spanning Feb 15–Apr 14); embed pipeline confirmed operational with exit code 0 and ready to resume April data ingestion.
root_cause: ''
solution: LightRAG incremental pipeline watermark stuck at 2026-03-04 06:17:44 in knowledge_embeddings processing (1.37M rows
  spanning Feb 15–Apr 14); embed pipeline confirmed operational with exit code 0 and ready to resume April data ingestion.
files_changed: []
detection_method: Watermark freeze prevented April rows from entering graph; pipeline skipping 200/200 batches already in
  LightRAG.
detection_agent: mnemosyne
resolution_time_minutes: null
related_fixes: []
lessons_learned: ''
prevention: Monitor lightrag_incremental_update.py state file for watermark drift >7 days in future runs.
extensions:
  source_file: lightrag_watermark_march_stuck_fixed_2026_04_14.md
  source_title: Lightrag Watermark March Stuck Fixed 2026 04 14
source_session: f8d72e5a-245a-47bc-b42d-66b17b8f35fb
---
