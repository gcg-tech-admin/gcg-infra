---
id: fix-2026-06-01-quality-scorer-fix-task-2-3
date: '2026-06-01T20:48:52Z'
agent: mnemosyne
severity: crit
status: resolved
systems:
- agent-fleet
- embedding-pipeline
tags:
- agent-fleet
- embedding
symptom: Fixed `/opt/gcg/shared/scripts/quality_scorer.py` to score `agent_memory` instead of the frozen `knowledge_embeddings`
  table, with 1.23M active rows currently at 0% scored; added --status flag for monitoring, --dry-run for distribution preview,
  and time guard blocking execution during 03:30–05:00 UTC embed window to protect cascade operations.
root_cause: ''
solution: 'Fixed `/opt/gcg/shared/scripts/quality_scorer.py` to score `agent_memory` instead of the frozen `knowledge_embeddings`
  table, with 1.23M active rows currently at 0% scored; added --status flag for monitoring, --dry-run for distribution preview,
  and time guard blocking execution during 03:30–05:00 UTC embed window to protect cascade operations.

  '
files_changed:
- path: /opt/gcg/shared/scripts/quality_scorer.py
  action: modified
detection_method: ''
detection_agent: mnemosyne
resolution_time_minutes: null
related_fixes: []
lessons_learned: ''
prevention: ''
extensions:
  source_file: quality_scorer_fix_task_2_3.md
  source_title: Quality Scorer Fix Task 2 3
source_session: ''
---
