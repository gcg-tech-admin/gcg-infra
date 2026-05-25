#!/bin/bash
# Mirror storagebox to Sentinel via AX102 relay
set -euo pipefail
LOG=/var/log/gcg-sentinel-mirror.log
echo "=== $(date -u) Starting mirror ===" >> $LOG

# Step 1: Sync from storagebox to AX102 local temp
rsync -avz --delete   -e 'ssh -i /root/.ssh/storagebox_borg -p 23 -o StrictHostKeyChecking=no'   u547533@u547533.your-storagebox.de:gcg-borg-2026-05-18/ /opt/gcg/sentinel-cache/gcg-borg/   2>&1 | tail -3 >> $LOG

rsync -avz --delete   -e 'ssh -i /root/.ssh/storagebox_borg -p 23 -o StrictHostKeyChecking=no'   u547533@u547533.your-storagebox.de:backups/ /opt/gcg/sentinel-cache/backups/   2>&1 | tail -3 >> $LOG

# Step 2: Push to Sentinel
rsync -avz --delete   -e 'ssh -i /root/.ssh/gcg-sentinel-outbound-20260524 -o StrictHostKeyChecking=no'   /opt/gcg/sentinel-cache/ root@65.21.60.52:/opt/sentinel/mirror/   2>&1 | tail -3 >> $LOG

echo "=== $(date -u) Mirror complete ===" >> $LOG
