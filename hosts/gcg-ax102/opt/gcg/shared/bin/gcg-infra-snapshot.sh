#!/bin/bash
# Auto-snapshot fleet configs into /opt/gcg/infra-repo + push to GitHub.
# Runs daily at 04:00 Dubai via gcg-infra-snapshot.timer.
set -euo pipefail

REPO=/opt/gcg/infra-repo
HOST=$(hostname -s | tr "[:upper:]" "[:lower:]")
HOSTDIR="$REPO/hosts/$HOST"
LOG=/var/log/gcg-infra-snapshot.log

exec >> "$LOG" 2>&1
echo ""
echo "[$(date -u +%FT%TZ)] snapshot starting (host=$HOST)"

mkdir -p "$HOSTDIR/etc/systemd/system" "$HOSTDIR/etc/credstore.encrypted" \
         "$HOSTDIR/etc/ssh" "$HOSTDIR/opt/gcg/shared/bin" "$HOSTDIR/opt/gcg/openclaw"

# Sync openclaw.json per agent (skip everything else under each agent dir)
for d in /opt/gcg/openclaw-*/; do
  agent=$(basename "$d")
  mkdir -p "$HOSTDIR/opt/gcg/$agent"
  # SKIPPED — plaintext secrets, see runbook TODO
done

# Systemd units (openclaw + gcg)
rsync -a --delete \
  --include="openclaw-*.service" --include="openclaw-*.service.d/" --include="openclaw-*.service.d/*" \
  --include="gcg-*.service" --include="gcg-*.timer" --include="gcg-*.service.d/" --include="gcg-*.service.d/*" \
  --include="*/" --exclude="*" \
  /etc/systemd/system/ "$HOSTDIR/etc/systemd/system/"

# Drop files known to contain plaintext secrets (TODO refactor source files)
rm -f "$HOSTDIR/etc/systemd/system/gcg-google-broker.service"
rm -rf "$HOSTDIR/etc/systemd/system/gcg-google-broker.service.d"

# Encrypted credstore blobs (opaque — safe)
rsync -a --delete /etc/credstore.encrypted/ "$HOSTDIR/etc/credstore.encrypted/" 2>/dev/null || true
[ -d /etc/credstore.holding ] && rsync -a --delete /etc/credstore.holding/ "$HOSTDIR/etc/credstore.holding/" || true

# Shared scripts + FLEET docs
rsync -a --delete /opt/gcg/shared/bin/ "$HOSTDIR/opt/gcg/shared/bin/" 2>/dev/null || true
for f in FLEET.yaml FLEET_INDEX.md; do
  [ -f "/opt/gcg/shared/$f" ] && cp "/opt/gcg/shared/$f" "$HOSTDIR/opt/gcg/shared/$f"
done

# SSH server config (NOT keys)
[ -f /etc/ssh/sshd_config ] && cp /etc/ssh/sshd_config "$HOSTDIR/etc/ssh/sshd_config"
[ -d /etc/ssh/sshd_config.d ] && rsync -a /etc/ssh/sshd_config.d/ "$HOSTDIR/etc/ssh/sshd_config.d/"

# Network / firewall hints
[ -f /etc/hosts ] && cp /etc/hosts "$HOSTDIR/etc/hosts"
[ -f /etc/hostname ] && cp /etc/hostname "$HOSTDIR/etc/hostname"

# nftables/iptables ruleset dump (read-only)
nft list ruleset > "$HOSTDIR/etc/nftables.ruleset.txt" 2>/dev/null || iptables-save > "$HOSTDIR/etc/iptables.ruleset.txt" 2>/dev/null || true

# Package list (so we know what was installed)
dpkg --get-selections | grep -v deinstall > "$HOSTDIR/etc/dpkg.selections.txt" 2>/dev/null || true

cd "$REPO"
git add -A
if git diff --cached --quiet; then
  echo "  no changes — nothing to commit"
else
  git commit -m "snapshot $HOST $(date -u +%FT%TZ)" 2>&1 | tail -2

  if git remote get-url origin >/dev/null 2>&1; then
    GIT_SSH_COMMAND="ssh -i /root/.ssh/github_gcg_infra -o StrictHostKeyChecking=accept-new" \
      git push origin main 2>&1 | tail -5 || echo "  push failed (remote not configured?)"
  else
    echo "  NOTE: no remote set yet — commit is local only. Configure: git remote add origin git@github.com:USER/gcg-infra.git"
  fi
fi
echo "[$(date -u +%FT%TZ)] snapshot done"
