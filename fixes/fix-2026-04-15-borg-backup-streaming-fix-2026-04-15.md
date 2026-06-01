---
id: fix-2026-04-15-borg-backup-streaming-fix-2026-04-15
date: '2026-04-15T00:00:00Z'
agent: mnemosyne
severity: crit
status: resolved
systems:
- api-gateway
- backup
- infrastructure
tags:
- infrastructure
- backup
- api
symptom: Fixed `/opt/gcg/shared/scripts/borg_backup.sh` to stream pg_dump output directly to borg via stdin using `--stdin-name`
  flag, eliminating the 20GB temp file write to /var that pushed disk to 99% on Apr 7 and Apr 15. Refactored from single `db-${DATE}`
  archive to two separate archives (`db-intelligence-${DATE}` + `db-crm-${DATE}`). Script deployed and operational (PID 21197);
  `/var/opt/gcg/backups/tmp` remains empty.
root_cause: ''
solution: '`/opt/gcg/shared/scripts/borg_backup.sh` to stream pg_dump output directly to borg via stdin using `--stdin-name`
  flag, eliminating the 20GB temp file write to /var that pushed disk to 99% on Apr 7 and Apr 15. Refactored from single `db-${DATE}`
  archive to two separate archives (`db-intelligence-${DATE}` + `db-crm-${DATE}`). Script deployed and operational (PID 21197);
  `/var/opt/gcg/backups/tmp` remains empty.'
files_changed:
- path: /opt/gcg/shared/scripts/borg_backup.sh
  action: modified
- path: /opt/gcg/backups/tmp`
  action: modified
detection_method: Recurring /var disk saturation (99%) was critical operational risk; streaming eliminates temp file footprint
  and pre-flight disk checks.
detection_agent: mnemosyne
resolution_time_minutes: null
related_fixes: []
lessons_learned: ''
prevention: Monitor /var disk in future backup operations; stdin streaming approach is now production standard for large database
  dumps.
extensions:
  source_file: borg_backup_streaming_fix_2026_04_15.md
  source_title: Borg Backup Streaming Fix 2026 04 15
source_session: b1ba7d8f-e8ef-484b-83c0-c9cb0f6e91a8
---
