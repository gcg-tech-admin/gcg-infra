#!/bin/bash
# DEPRECATED 2026-05-25 — Sentinel is now reachable ONLY from Mac Mini Daen (firewalled
# off from AX42/AX102 post-Kinsing). The DR mirror is owned by Mac Mini Daen, which pulls
# the storagebox into its local mirror and replicates onward to Sentinel from there.
# This script must NOT run from AX102. The corresponding systemd unit is masked.
# If you need to verify the Sentinel mirror, ask Mac Mini Daen — see project memory
# project-sentinel-mirror-macmini.
echo "[$(date -u +%FT%TZ)] gcg-sentinel-mirror.sh is DEPRECATED on AX102; refusing to run." >&2
exit 1
set -euo pipefail
LOG=/var/log/gcg-sentinel-mirror.log
echo "=== $(date -u) Starting mirror ===" >> $LOG

# Step 1: Sync from storagebox to AX102 local temp
rsync -avz --delete   -e 'ssh -i /root/.ssh/storagebox_borg -p 23 -o StrictHostKeyChecking=no'   u547533@u547533.your-storagebox.de:gcg-borg-2026-05-18/ /opt/gcg/sentinel-cache/gcg-borg/   2>&1 | tail -3 >> $LOG

rsync -avz --delete   -e 'ssh -i /root/.ssh/storagebox_borg -p 23 -o StrictHostKeyChecking=no'   u547533@u547533.your-storagebox.de:backups/ /opt/gcg/sentinel-cache/backups/   2>&1 | tail -3 >> $LOG

# Step 2: Push to Sentinel
rsync -avz --delete   -e 'ssh -i /root/.ssh/gcg-sentinel-outbound-20260524 -o StrictHostKeyChecking=no'   /opt/gcg/sentinel-cache/ root@65.21.60.52:/opt/sentinel/mirror/   2>&1 | tail -3 >> $LOG

echo "=== $(date -u) Mirror complete ===" >> $LOG
