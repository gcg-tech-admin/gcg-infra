#!/bin/bash
# Disk alert for AX42 — checks /var via vSwitch SSH, alerts Peter via fleet if >80%
set -euo pipefail
SSH_KEY="/etc/gcg/fleet-ax42-access"
THRESHOLD=80
LOGFILE="/var/log/gcg-disk-alert-ax42.log"
STAMP=$(date -Iseconds)

CURRENT=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=8 root@10.0.0.2 \
    "df /var --output=pcent | tail -1 | tr -dc '0-9'" 2>/dev/null)

if [ -z "$CURRENT" ]; then
    echo "[$STAMP] ERROR: Could not read /var usage from AX42" >> "$LOGFILE"
    exit 1
fi

if [ "$CURRENT" -gt "$THRESHOLD" ]; then
    SENT_FLAG="/tmp/.gcg-disk-alert-ax42-sent"
    if [ ! -f "$SENT_FLAG" ] || [ "$(find "$SENT_FLAG" -mmin +60 2>/dev/null)" ]; then
        echo "[$STAMP] ALERT: AX42 /var at ${CURRENT}% (>${THRESHOLD}%)" >> "$LOGFILE"
        /opt/gcg/shared/bin/fleet send --priority 1 peter "AX42 DISK ALERT: /var is at ${CURRENT}% (threshold ${THRESHOLD}%). Immediate attention needed." >> "$LOGFILE" 2>&1 || true
        touch "$SENT_FLAG"
    fi
else
    rm -f /tmp/.gcg-disk-alert-ax42-sent
fi
