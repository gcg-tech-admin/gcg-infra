---
id: fix-2026-04-15-mnemosyne-session-cleanup-fix-2026-04-15
date: '2026-04-15T00:00:00Z'
agent: mnemosyne
severity: warn
status: resolved
systems:
- agent-fleet
- embedding-pipeline
tags:
- agent-fleet
- embedding
symptom: 'Fixed Mnemosyne session cleanup script (HIGH priority, 2026-04-15 08:51). Script was truncating content inside JSONL
  files instead of deleting old session files. Root causes: missing `.openclaw` path (daen 421MB, nik 213MB untouched), no
  count-based limit enforcement, checkpoint files not matched, orphaned `.deleted.*` files. Fixed by adding second path per
  agent, implementing 50-file mtime-based keep limit, explicit checkpoint deletion, and `.deleted.*` cleanup pass. Results:
  daen 421MB→66MB (freed 349MB, 1763 files deleted), all agent dirs normalized to exactly 50 files; large remainder (daen
  66MB, nik 60MB) is expected from legitimate large sessions (Nik voice transcripts, Daen architect-level context ~1.3MB/session
  avg).'
root_cause: ''
solution: 'Fixed Mnemosyne session cleanup script (HIGH priority, 2026-04-15 08:51). Script was truncating content inside
  JSONL files instead of deleting old session files. Root causes: missing `.openclaw` path (daen 421MB, nik 213MB untouched),
  no count-based limit enforcement, checkpoint files not matched, orphaned `.deleted.*` files. Fixed by adding second path
  per agent, implementing 50-file mtime-based keep limit, explicit checkpoint deletion, and `.deleted.*` cleanup pass. Results:
  daen 421MB→66MB (f'
files_changed: []
detection_method: ''
detection_agent: mnemosyne
resolution_time_minutes: null
related_fixes: []
lessons_learned: ''
prevention: ''
extensions:
  source_file: mnemosyne_session_cleanup_fix_2026_04_15.md
  source_title: Mnemosyne Session Cleanup Fix 2026 04 15
source_session: 80ae0163-f986-4624-a972-380112095d92
---
