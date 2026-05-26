#!/bin/bash
# DEPRECATED 2026-05-25 — Sentinel is reachable ONLY from Mac Mini Daen post-Kinsing.
# DR mirror is owned by Mac Mini Daen (pulls storagebox → local mirror → Sentinel).
# This script must NOT run from AX102. See project-sentinel-mirror-macmini.
echo "[$(date -u +%FT%TZ)] gcg-sentinel-sync.sh is DEPRECATED on AX102; refusing to run." >&2
exit 1
set -euo pipefail
SENTINEL_IP=65.21.60.52
SENTINEL_KEY=/root/.ssh/gcg-sentinel-outbound-20260524
SB_KEY=/root/.ssh/storagebox_borg
LOG=/var/log/gcg-sentinel-sync.log

echo "=== $(date -u) Starting Sentinel sync ===" >> $LOG

# Sync borg repo
rsync -avz --delete -e "ssh -i $SENTINEL_KEY -o StrictHostKeyChecking=no" \
  --rsync-path="ssh -i $SB_KEY -p 23 -o StrictHostKeyChecking=no u547533@u547533.your-storagebox.de rsync" \
  u547533@u547533.your-storagebox.de:gcg-borg-2026-05-18/ root@$SENTINEL_IP:/opt/sentinel/mirror/gcg-borg/ \
  2>&1 | tail -5 >> $LOG

# Sync backups dir
rsync -avz --delete -e "ssh -i $SENTINEL_KEY -o StrictHostKeyChecking=no" \
  --rsync-path="ssh -i $SB_KEY -p 23 -o StrictHostKeyChecking=no u547533@u547533.your-storagebox.de rsync" \
  u547533@u547533.your-storagebox.de:backups/ root@$SENTINEL_IP:/opt/sentinel/mirror/backups/ \
  2>&1 | tail -5 >> $LOG

echo "=== $(date -u) Sentinel sync complete ===" >> $LOG
