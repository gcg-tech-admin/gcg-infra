# Migration Report — Phase 3
**Date:** 2026-06-01 20:48 UTC
**Source:** /opt/gcg/shared/docs/research/claude-global-memory
**Destination:** /opt/gcg/infra-repo/fixes
**Entries migrated:** 14

## Registry Entries
- `fix-2026-04-12-auto-recall-v2-crash-fix-2026-04-12` — Fixed 4 critical bugs in `/opt/gcg/shared/gcg_tools/auto_recall_v2.py` that cras
- `fix-2026-04-13-beeper-auth-lockfix-2026-04-13` — Fixed 3 bugs in `/opt/gcg/shared/scripts/communications_ingestor.py`: removed br
- `fix-2026-04-15-borg-backup-streaming-fix-2026-04-15` — Fixed `/opt/gcg/shared/scripts/borg_backup.sh` to stream pg_dump output directly
- `fix-2026-04-08-crm-frontend-backend-fix-2026-04-08` — Fixed CRM Frontend 500 error by adding missing `crmCoreApi` client (commit 4dd76
- `fix-2026-04-13-embed-pipeline-crit-fix-2026-04-13` — Resolved critical embedding pipeline error spike (99 errors in 24h vs. 5-error t
- `fix-2026-04-15-kb-taxonomy-embed-fix-2026-04-15` — Fixed KB taxonomy violations and embedding pipeline failures triggered by hourly
- `fix-2026-04-12-lightrag-fix-2026-04-12` — that had been failing since Apr 3 with `Another incremental update is already ru
- `fix-2026-04-14-lightrag-watermark-march-stuck-fixed-2026-04-14` — Fixed LightRAG incremental pipeline watermark stuck at 2026-03-04 06:17:44 in kn
- `fix-2026-04-15-mnemosyne-session-cleanup-fix-2026-04-15` — Fixed Mnemosyne session cleanup script (HIGH priority, 2026-04-15 08:51). Script
- `fix-2026-06-01-pattern-enforcer-fix-task-5-va` — Fixed pattern_enforcer.py check_dispatch() function by broadening AP-001 regex f
- `fix-2026-06-01-quality-scorer-fix-task-2-3` — Fixed `/opt/gcg/shared/scripts/quality_scorer.py` to score `agent_memory` instea
- `fix-2026-06-01-task-1-5-stale-agents-fix` — Fixed malformed @tom agent_name entries (2 rows) and archived 58 test artifact r
- `fix-2026-06-01-task-1-5-stale-agents-fix-attempt-2` — Found and re-embedded 1 stale agent (hector at 11 days stale) via embed_v2.py; a
- `fix-2026-06-01-viktor-broker-alert-fix` — Viktor was sending repeated false CRITICAL alerts about the Google Broker being 

## Files to Archive
- `auto_recall_v2_crash_fix_2026_04_12.md` → /opt/gcg/shared/docs/research/claude-global-memory/.archived/
- `beeper_auth_lockfix_2026_04_13.md` → /opt/gcg/shared/docs/research/claude-global-memory/.archived/
- `borg_backup_streaming_fix_2026_04_15.md` → /opt/gcg/shared/docs/research/claude-global-memory/.archived/
- `crm_frontend_backend_fix_2026_04_08.md` → /opt/gcg/shared/docs/research/claude-global-memory/.archived/
- `embed_pipeline_crit_fix_2026_04_13.md` → /opt/gcg/shared/docs/research/claude-global-memory/.archived/
- `kb_taxonomy_embed_fix_2026_04_15.md` → /opt/gcg/shared/docs/research/claude-global-memory/.archived/
- `lightrag_fix_2026_04_12.md` → /opt/gcg/shared/docs/research/claude-global-memory/.archived/
- `lightrag_watermark_march_stuck_fixed_2026_04_14.md` → /opt/gcg/shared/docs/research/claude-global-memory/.archived/
- `mnemosyne_session_cleanup_fix_2026_04_15.md` → /opt/gcg/shared/docs/research/claude-global-memory/.archived/
- `pattern_enforcer_fix_task_5_va.md` → /opt/gcg/shared/docs/research/claude-global-memory/.archived/
- `quality_scorer_fix_task_2_3.md` → /opt/gcg/shared/docs/research/claude-global-memory/.archived/
- `task_1_5_stale_agents_fix.md` → /opt/gcg/shared/docs/research/claude-global-memory/.archived/
- `task_1_5_stale_agents_fix_attempt_2.md` → /opt/gcg/shared/docs/research/claude-global-memory/.archived/
- `viktor_broker_alert_fix.md` → /opt/gcg/shared/docs/research/claude-global-memory/.archived/

## Verification
- [ ] All 14 entries reviewed by Mnemosyne (Phase 3.2)
- [ ] Originais archived to .archived/ (Phase 3.3)
- [ ] INDEX.md regenerated (Phase 3.4)