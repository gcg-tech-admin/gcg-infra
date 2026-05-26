#!/bin/bash
# Pull AX42 host-level config to AX102 staging dir for inclusion in nightly borg.
# Runs as ExecStartPre of gcg-borg-backup.service.
set -euo pipefail

STAGE=/var/lib/gcg-backup-staging/ax42
SSH_KEY=/etc/gcg/fleet-ax42-access
AX42=root@10.0.0.2
LOG=/var/log/gcg-ax42-config-sync.log

exec >> "$LOG" 2>&1
echo ""
echo "[$(date -u +%FT%TZ)] ax42 config sync starting"

mkdir -p "$STAGE"

PATHS=(
  /etc/systemd/system/gcg-pg-dump.service
  /etc/systemd/system/gcg-pg-dump.timer
  /etc/systemd/system/openclaw-
  /opt/gcg/shared/bin/gcg-pg-dump.sh
  /etc/docker/daemon.json
  /etc/hosts
  /etc/fstab
  /etc/network/interfaces
  /etc/netplan
)

# Use rsync per-path with --ignore-missing-args; some paths may not exist.
for p in "${PATHS[@]}"; do
  rsync -aR --ignore-missing-args \
    -e "ssh -i $SSH_KEY -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15" \
    "$AX42:$p" "$STAGE/" 2>&1 | tail -3 || true
done

# Pull all gcg-* and openclaw-* systemd units via wildcard expansion on the remote
ssh -i "$SSH_KEY" -o ConnectTimeout=15 "$AX42" \
  'tar cf - /etc/systemd/system/gcg-*.service /etc/systemd/system/gcg-*.timer \
              /etc/systemd/system/openclaw-*.service /etc/systemd/system/openclaw-*.service.d \
              /opt/gcg/shared/bin/ /opt/gcg/shared/docs/ 2>/dev/null' \
  | tar xf - -C "$STAGE/" 2>&1 | tail -3 || true

# Runtime manifest: container inventory + compose files (data omitted — DB dumped separately)
ssh -i "$SSH_KEY" -o ConnectTimeout=15 "$AX42" \
  'echo "=== docker ps ==="; docker ps --format "{{.Names}}\t{{.Image}}\t{{.Status}}" 2>/dev/null; \
   echo "=== compose files ==="; find /opt/gcg -maxdepth 4 \( -name "docker-compose*.yml" -o -name "compose*.yml" \) 2>/dev/null | while read f; do echo "--- $f ---"; cat "$f"; done' \
  > "$STAGE/ax42-runtime-manifest.txt" 2>&1 || true

echo "[$(date -u +%FT%TZ)] ax42 config sync complete, staged at $STAGE (size: $(du -sh "$STAGE" 2>/dev/null | cut -f1))"
