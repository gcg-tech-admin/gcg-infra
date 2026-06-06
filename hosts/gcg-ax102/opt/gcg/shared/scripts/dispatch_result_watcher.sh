#!/bin/bash
# Watch AX42:/opt/gcg/shared/dispatch/result.md for new dispatches.
# When mtime changes, wake Daen's session with a concise summary.
# Runs every 1 min via cron.
set -euo pipefail

STATE=/opt/gcg/shared/state/dispatch/result_watcher.last
REMOTE=root@10.0.0.2
REMOTE_PATH=/opt/gcg/shared/dispatch/result.md

# Get remote mtime (epoch)
REMOTE_MTIME=$(ssh -o ConnectTimeout=5 "$REMOTE" "stat -c %Y $REMOTE_PATH 2>/dev/null" 2>/dev/null || echo "")
[ -z "$REMOTE_MTIME" ] && exit 0

LAST_MTIME=$(cat "$STATE" 2>/dev/null || echo "0")

if [ "$REMOTE_MTIME" -gt "$LAST_MTIME" ]; then
  # Fetch the header line + first 400 chars for preview
  PREVIEW=$(ssh "$REMOTE" "head -c 1500 $REMOTE_PATH" 2>/dev/null | head -5 | tr '\n' ' ' | head -c 400)

  MSG="📨 Dispatch result.md updated on AX42 at $(date -d @$REMOTE_MTIME '+%H:%M:%S GST'). Preview: ${PREVIEW}... Check via: ssh -i /etc/gcg/fleet-ax42-access root@10.0.0.2 'cat /opt/gcg/shared/dispatch/result.md'"

  fleet wake daen "$MSG" >/dev/null 2>&1 || true

  echo "$REMOTE_MTIME" > "$STATE"
fi
