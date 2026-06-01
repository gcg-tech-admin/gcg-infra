---
id: fix-2026-04-13-embed-pipeline-crit-fix-2026-04-13
date: '2026-04-13T00:00:00Z'
agent: mnemosyne
severity: crit
status: resolved
systems:
- agent-fleet
- api-gateway
- embedding-pipeline
tags:
- agent-fleet
- embedding
- api
symptom: 'Resolved critical embedding pipeline error spike (99 errors in 24h vs. 5-error threshold) by identifying Gemini
  Embedding API daily quota exhaustion in nightly `embed_all_sessions.sh` batch (329K chunks, 110K+ API calls in 7.5h). **Why:**
  Insufficient backoff logic allowed rate-limit retries to exhaust quota without recovery window. **How to apply:** Monitor
  `/opt/gcg/shared/logs/embed.log` for sustained >20% error rates; killed stuck batch + orphaned embed processes + stale lockfiles;
  added 60s cooldown to `embed_v2.py` post-rate-limit-exhaustion (was 0s) and 30s delay to `embed_all_sessions.sh` post-failure
  path (success path already had 15s); originals backed up to `/opt/gcg/backups/`. Current pipeline status: 4.9% error rate
  (117 errors / 2,277 successes), 100 sessions with 0 rows written auto-retry in next batch when Gemini quota resets.'
root_cause: ''
solution: Resolved critical embedding pipeline error spike (99 errors in 24h vs. 5-error threshold) by identifying Gemini
  Embedding API daily quota exhaustion in nightly `embed_all_sessions.sh` batch (329K chunks, 110K+ API calls in 7.5h). **Why:**
  Insufficient backoff logic allowed rate-limit retries to exhaust quota without recovery window. **How to apply:** Monitor
  `/opt/gcg/shared/logs/embed.log` for sustained >20% error rates; killed stuck batch + orphaned embed processes + stale lockfiles;
  added 60s
files_changed:
- path: /opt/gcg/shared/logs/embed.log
  action: modified
- path: /opt/gcg/backups/
  action: modified
detection_method: ''
detection_agent: mnemosyne
resolution_time_minutes: null
related_fixes: []
lessons_learned: ''
prevention: 'Monitor `/opt/gcg/shared/logs/embed.log` for sustained >20% error rates; killed stuck batch + orphaned embed
  processes + stale lockfiles; added 60s cooldown to `embed_v2.py` post-rate-limit-exhaustion (was 0s) and 30s delay to `embed_all_sessions.sh`
  post-failure path (success path already had 15s); originals backed up to `/opt/gcg/backups/`. Current pipeline status: 4.9%
  error rate (117 errors / 2,277 successes), 100 sessions with 0 rows written auto-retry in next batch when Gemini quota resets.'
extensions:
  source_file: embed_pipeline_crit_fix_2026_04_13.md
  source_title: Embed Pipeline Crit Fix 2026 04 13
source_session: ababb6d2-7d91-4856-a72b-d6b06e7cd5d8
---
