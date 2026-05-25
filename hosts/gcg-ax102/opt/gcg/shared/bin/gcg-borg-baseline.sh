#!/bin/bash
# One-shot: create immutable baseline tag for post-incident known-good state.
set -euo pipefail
[ -z "${CREDENTIALS_DIRECTORY:-}" ] && { echo "ERROR: needs systemd LoadCredentialEncrypted="; exit 2; }
export BORG_PASSPHRASE=$(cat "$CREDENTIALS_DIRECTORY/borg-passphrase")
export BORG_RSH="ssh -4 -i /root/.ssh/storagebox_borg_appendonly -p 23 -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15"
REPO="ssh://u547533@u547533.your-storagebox.de/./gcg-borg-2026-05-18"

echo "[$(date -u +%FT%TZ)] creating baseline-2026-05-18 tag..."
borg create --comment "post-incident baseline, known good (2026-05-18)" \
  --stats --compression zstd,6 \
  --exclude-caches \
  --exclude "/opt/gcg/openclaw-*/state" \
  --exclude "/opt/gcg/openclaw-*/npm" \
  --exclude "/opt/gcg/openclaw-*/node_modules" \
  --exclude "/opt/gcg/openclaw-*/logs/*.log" \
  --exclude "/opt/gcg/openclaw-*/logs/*.log.[0-9]*" \
  --exclude "**/venv" --exclude "**/.venv" --exclude "**/node_modules" \
  --exclude "**/__pycache__" --exclude "**/.pytest_cache" \
  --exclude "**/daen-venv" --exclude "**/my_env" --exclude "**/gcg_env" --exclude "**/gcg_temp_env" --exclude "**/daen_recall_venv" \
  --exclude "**/.git/objects/pack/*.pack" \
  --exclude "**/*.bak" --exclude "**/*.bak.*" \
  --exclude "**/*.mp4" --exclude "**/*.mov" --exclude "**/*.mkv" \
  --exclude "/opt/gcg/restore-*" --exclude "/opt/gcg/_archived" --exclude "/opt/gcg/_audit" \
  --exclude "/opt/gcg/intelligence.prod-snapshot-*" \
  --exclude "/opt/gcg/shared/venv" --exclude "/opt/gcg/nik-repo" \
  "$REPO"::baseline-2026-05-18 \
  /opt/gcg /etc/systemd/system /etc/credstore.encrypted /etc/ssh/sshd_config /etc/hosts

echo "[$(date -u +%FT%TZ)] baseline tag created"
borg list --short "$REPO"
