---
id: fix-2026-04-13-beeper-auth-lockfix-2026-04-13
date: '2026-04-13T00:00:00Z'
agent: mnemosyne
severity: crit
status: resolved
systems:
- api-gateway
- cron-scheduler
- infrastructure
- postgresql
tags:
- infrastructure
- postgresql
- cron
- api
symptom: 'Fixed 3 bugs in `/opt/gcg/shared/scripts/communications_ingestor.py`: removed broken internal flock check (cron''s
  `flock -n` handles mutual exclusion), changed API response key from "chats"/"messages" to "items" and fixed channel derivation
  to use `accountID`/`isSender`, replaced `conn.rollback()` with PostgreSQL SAVEPOINTs to prevent cascade deletion. Auth token
  at `/opt/gcg/shared/credentials/beeper_api_token.txt` was valid; script simply never reached API call due to lock logic
  error. **Result:** 486 messages ingested (230 telegram-in, 36 telegram-out, 211 whatsapp-in, 9 whatsapp-out), 26 chats tracked,
  pipeline auto-resumes every 30min cron.'
root_cause: ''
solution: '3 bugs in `/opt/gcg/shared/scripts/communications_ingestor.py`: removed broken internal flock check (cron''s `flock
  -n` handles mutual exclusion), changed API response key from "chats"/"messages" to "items" and fixed channel derivation
  to use `accountID`/`isSender`, replaced `conn.rollback()` with PostgreSQL SAVEPOINTs to prevent cascade deletion. Auth token
  at `/opt/gcg/shared/credentials/beeper_api_token.txt` was valid; script simply never reached API call due to lock logic
  error. **Result:** 486 messages ingested (230 telegram-in, 36 telegram-out, 211 whatsapp-in, 9 whatsapp-out), 26 chats tracked,
  pipeline auto-resumes every 30min cron.'
files_changed:
- path: /opt/gcg/shared/scripts/communications_ingestor.py
  action: modified
- path: /opt/gcg/shared/credentials/beeper_api_token.txt
  action: modified
detection_method: Communications ingestor was broken for 5 days due to lock/API key mismatches blocking real-time Beeper message
  flow into agent_memory.
detection_agent: mnemosyne
resolution_time_minutes: null
related_fixes: []
lessons_learned: ''
prevention: Cron ingestor now healthy; monitor next 3 runs for stability. If messages drop again, check Beeper API token refresh
  schedule and SAVEPOINT behavior under high volume.
extensions:
  source_file: beeper_auth_lockfix_2026_04_13.md
  source_title: Beeper Auth Lockfix 2026 04 13
source_session: 8b794d01-6d21-4664-882d-0a681e5a7193
---
