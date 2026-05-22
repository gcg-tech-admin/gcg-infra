#!/bin/bash
# Hourly Borg backup of /opt/gcg + /etc/credstore.encrypted + systemd units
# Retention: daily 14 / weekly 8 / monthly 6
set -euo pipefail
[ -z "${CREDENTIALS_DIRECTORY:-}" ] && { echo "ERROR: needs systemd LoadCredentialEncrypted="; exit 2; }
export BORG_PASSPHRASE=$(cat "$CREDENTIALS_DIRECTORY/borg-passphrase")
export BORG_RSH="ssh -4 -i /root/.ssh/storagebox_borg_appendonly -p 23 -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15"
REPO="ssh://u547533@u547533.your-storagebox.de/./gcg-borg-2026-05-18"

STAMP=$(date -u +%Y%m%dT%H%M%S)
HN=$(hostname -s)

echo "[$(date -u +%FT%TZ)] borg create $HN-$STAMP"
borg create --stats --compression zstd,6 --exclude-caches \
  --exclude "/opt/gcg/openclaw-*/state" \
  --exclude "/opt/gcg/openclaw-*/npm" \
  --exclude "/opt/gcg/openclaw-*/node_modules" \
  --exclude "/opt/gcg/openclaw-*/logs/*.log" \
  --exclude "/opt/gcg/openclaw-*/logs/*.log.[0-9]*" \
  --exclude "**/venv" \
  --exclude "**/.venv" \
  --exclude "**/node_modules" \
  --exclude "**/__pycache__" \
  --exclude "**/.pytest_cache" \
  --exclude "**/daen-venv" \
  --exclude "**/my_env" \
  --exclude "**/gcg_env" \
  --exclude "**/gcg_temp_env" \
  --exclude "**/daen_recall_venv" \
  --exclude "**/.git/objects/pack/*.pack" \
  --exclude "**/*.bak" \
  --exclude "**/*.bak.*" \
  --exclude "**/*.mp4" \
  --exclude "**/*.mov" \
  --exclude "**/*.mkv" \
  --exclude "/opt/gcg/restore-*" \
  --exclude "/opt/gcg/shared/venv" \
  --exclude "/opt/gcg/_archived" \
  --exclude "/opt/gcg/_audit" \
  --exclude "/opt/gcg/intelligence.prod-snapshot-*" \
  --exclude "/run" --exclude "/tmp" --exclude "/var/cache" \
  "$REPO::${HN}-${STAMP}" \
  /opt/gcg /etc/credstore.encrypted /etc/credstore.holding \
  /etc/systemd/system/openclaw-*.service /etc/systemd/system/openclaw-*.service.d \
  /etc/systemd/system/gcg-*.service /etc/systemd/system/gcg-*.timer \
  /root/.ssh/storagebox_borg_appendonly.pub 2>&1 | tail -10

