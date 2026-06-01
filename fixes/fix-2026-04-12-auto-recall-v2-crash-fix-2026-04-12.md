---
id: fix-2026-04-12-auto-recall-v2-crash-fix-2026-04-12
date: '2026-04-12T00:00:00Z'
agent: mnemosyne
severity: crit
status: resolved
systems:
- agent-fleet
- embedding-pipeline
tags:
- agent-fleet
- embedding
symptom: 'Fixed 4 critical bugs in `/opt/gcg/shared/gcg_tools/auto_recall_v2.py` that crashed when Gemini embeddings returned
  None: (B1) moved DB query before embeddings check, (B2) hardcoded .env path to `/opt/gcg/shared/.env`, (B3) added rollback
  to clear poisoned PG transaction state, (B4) fixed admin-scope query. Result: 26/28 agents fully repopulated with agent_memory
  + structured knowledge; mnemosyne needs DB provisioning, chiron received 1K rows (likely complete).'
root_cause: ''
solution: 'Fixed 4 critical bugs in `/opt/gcg/shared/gcg_tools/auto_recall_v2.py` that crashed when Gemini embeddings returned
  None: (B1) moved DB query before embeddings check, (B2) hardcoded .env path to `/opt/gcg/shared/.env`, (B3) added rollback
  to clear poisoned PG transaction state, (B4) fixed admin-scope query. Result: 26/28 agents fully repopulated with agent_memory
  + structured knowledge; mnemosyne needs DB provisioning, chiron received 1K rows (likely complete).

  '
files_changed:
- path: /opt/gcg/shared/gcg_tools/auto_recall_v2.py
  action: modified
- path: /opt/gcg/shared/.env
  action: modified
detection_method: ''
detection_agent: mnemosyne
resolution_time_minutes: null
related_fixes: []
lessons_learned: ''
prevention: ''
extensions:
  source_file: auto_recall_v2_crash_fix_2026_04_12.md
  source_title: Auto Recall V2 Crash Fix 2026 04 12
source_session: 8e4b6da2-56e6-4c69-aae7-3b085152d363
---
