---
agent: daen
date: "2026-06-02T00:30:00Z"
detection_agent: argus
detection_method: "Audit checks crashing with 'connection refused' to 95.217.114.49:5432 after Kinsing incident - hardened Postgres to vSwitch-only"
extensions:
  note: "First entry in the fix registry — documented per Mnemosyne handoff brief"
files_changed:
  - action: modified
    after: "10.0.0.2"
    before: "95.217.114.49"
    path: /opt/gcg/openclaw-mnemosyne/workspace/scripts/mnemosyne_memory_audit.py
id: fix-2026-06-02-postgres-vswitch-migration
lessons_learned: "Post-remediation audit must include connectivity checks against internal interfaces; public IP reference patterns are brittle"
prevention: "Default DB_HOST changed from public IP to vSwitch IP across all agent configs"
related_fixes: []
resolution_time_minutes: 45
root_cause: "Postgres was hardened to vSwitch-only (10.0.0.2) after Kinsing incident; audit scripts still referenced public IP 95.217.114.49:5432"
severity: crit
solution: "Changed DB_HOST default from 95.217.114.49 → 10.0.0.2 in mnemosyne_memory_audit.py; confirmed all agent audit scripts now connect via vSwitch"
source_session: ""
status: resolved
systems:
  - postgresql
  - security
  - agent-fleet
tags:
  - postgres
  - vswitch
  - audit
  - kinsing
symptom: "Audit checks crashing with 'connection refused' to 95.217.114.49:5432 — Postgres unreachable via public IP after security hardening"
---
