# Fix Registry Index
> Auto-generated. Update on every commit to `fixes/`.

| ID | Date | Agent | Severity | System | Symptom |
|---|---|---|---|---|---|
| [fix-2026-04-08-crm-frontend-backend-fix-2026-04-08](fix-2026-04-08-crm-frontend-backend-fix-2026-04-08.md) | 2026-04-08 | mnemosyne | warn | api-gateway, docker | Fixed CRM Frontend 500 error by adding missing `crmCoreApi`  |
| [fix-2026-04-12-auto-recall-v2-crash-fix-2026-04-12](fix-2026-04-12-auto-recall-v2-crash-fix-2026-04-12.md) | 2026-04-12 | mnemosyne | crit | agent-fleet, embedding-pipeline | Fixed 4 critical bugs in `/opt/gcg/shared/gcg_tools/auto_rec |
| [fix-2026-04-12-lightrag-fix-2026-04-12](fix-2026-04-12-lightrag-fix-2026-04-12.md) | 2026-04-12 | mnemosyne | crit | agent-fleet, embedding-pipeline | that had been failing since Apr 3 with `Another incremental  |
| [fix-2026-04-13-beeper-auth-lockfix-2026-04-13](fix-2026-04-13-beeper-auth-lockfix-2026-04-13.md) | 2026-04-13 | mnemosyne | crit | api-gateway, cron-scheduler | Fixed 3 bugs in `/opt/gcg/shared/scripts/communications_inge |
| [fix-2026-04-13-embed-pipeline-crit-fix-2026-04-13](fix-2026-04-13-embed-pipeline-crit-fix-2026-04-13.md) | 2026-04-13 | mnemosyne | crit | agent-fleet, api-gateway | Resolved critical embedding pipeline error spike (99 errors  |
| [fix-2026-04-14-lightrag-watermark-march-stuck-fixed-2026-04-14](fix-2026-04-14-lightrag-watermark-march-stuck-fixed-2026-04-14.md) | 2026-04-14 | mnemosyne | warn | agent-fleet, embedding-pipeline | Fixed LightRAG incremental pipeline watermark stuck at 2026- |
| [fix-2026-04-15-borg-backup-streaming-fix-2026-04-15](fix-2026-04-15-borg-backup-streaming-fix-2026-04-15.md) | 2026-04-15 | mnemosyne | crit | api-gateway, backup | Fixed `/opt/gcg/shared/scripts/borg_backup.sh` to stream pg_ |
| [fix-2026-04-15-kb-taxonomy-embed-fix-2026-04-15](fix-2026-04-15-kb-taxonomy-embed-fix-2026-04-15.md) | 2026-04-15 | mnemosyne | warn | agent-fleet, embedding-pipeline | Fixed KB taxonomy violations and embedding pipeline failures |
| [fix-2026-04-15-mnemosyne-session-cleanup-fix-2026-04-15](fix-2026-04-15-mnemosyne-session-cleanup-fix-2026-04-15.md) | 2026-04-15 | mnemosyne | warn | agent-fleet, embedding-pipeline | Fixed Mnemosyne session cleanup script (HIGH priority, 2026- |
| [fix-2026-06-01-pattern-enforcer-fix-task-5-va](fix-2026-06-01-pattern-enforcer-fix-task-5-va.md) | 2026-06-01 | mnemosyne | warn | backup, infrastructure | Fixed pattern_enforcer.py check_dispatch() function by broad |
| [fix-2026-06-01-quality-scorer-fix-task-2-3](fix-2026-06-01-quality-scorer-fix-task-2-3.md) | 2026-06-01 | mnemosyne | crit | agent-fleet, embedding-pipeline | Fixed `/opt/gcg/shared/scripts/quality_scorer.py` to score ` |
| [fix-2026-06-01-task-1-5-stale-agents-fix-attempt-2](fix-2026-06-01-task-1-5-stale-agents-fix-attempt-2.md) | 2026-06-01 | mnemosyne | warn | agent-fleet, embedding-pipeline | Found and re-embedded 1 stale agent (hector at 11 days stale |
| [fix-2026-06-01-task-1-5-stale-agents-fix](fix-2026-06-01-task-1-5-stale-agents-fix.md) | 2026-06-01 | mnemosyne | warn | agent-fleet, embedding-pipeline | Fixed malformed @tom agent_name entries (2 rows) and archive |
| [fix-2026-06-01-viktor-broker-alert-fix](fix-2026-06-01-viktor-broker-alert-fix.md) | 2026-06-01 | mnemosyne | crit | infrastructure | Viktor was sending repeated false CRITICAL alerts about the  |
| [fix-2026-06-02-postgres-vswitch-migration](fix-2026-06-02-postgres-vswitch-migration.md) | 2026-06-02 | daen | crit | postgresql, security | Audit checks crashing with 'connection refused' to 95.217.11 |

_15 entries_
