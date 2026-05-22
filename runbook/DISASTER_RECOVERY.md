# GCG Disaster Recovery Runbook

**Last updated:** 2026-05-22
**RTO target:** 4 hours total fleet from clean Hetzner provision
**Tested:** never (TODO: schedule quarterly drill)

## What you need before starting

| Item | Where it lives |
|---|---|
| 1Password master + hardware key | Peter physically |
| systemd-creds host key (`/var/lib/systemd/credential.secret`) | 1Password "GCG systemd-creds host key" item + USB in safe |
| Borg passphrase | 1Password "GCG Borg passphrase" |
| Storagebox SSH key (unrestricted) | Mac Mini at `~/.ssh/storagebox_macmini` |
| Hetzner Robot login | 1Password |
| GitHub access to `gcg-infra` repo | Deploy key on AX102/AX42 (see below) or PAT in 1Password |
| Cloudflared tunnel token | 1Password (TODO: not in repo, sanitized out) |
| **API keys per agent** (Anthropic, Perplexity, xAI, etc.) | 1Password vault (NOT in this repo — see "openclaw.json refactor" TODO) |

## Scenario 1 — Both AX102 + AX42 nuked (worst case)

### 1. Provision fresh Hetzner servers (~30 min)
Order new AX102 + AX42 in Hetzner Robot. Boot Ubuntu 24.04 minimal. Set hostnames `gcg-ax102` and `gcg-ax42`.

### 2. Restore host-bound secrets material (~10 min)
Place systemd-creds host key at `/var/lib/systemd/credential.secret` (chmod 600 root:root). Verify with `systemd-creds list`.

### 3. Clone infra repo (~5 min)
```bash
apt-get update && apt-get install -y git
# Use deploy key (regenerate if lost) or PAT
git clone git@github.com:gcg-tech-admin/gcg-infra.git /opt/gcg/infra-repo
```

### 4. Replay configs from snapshot (~20 min)
```bash
HOST=$(hostname -s)   # gcg-ax102 or gcg-ax42
cd /opt/gcg/infra-repo/hosts/$HOST
rsync -av etc/credstore.encrypted/ /etc/credstore.encrypted/
rsync -av etc/systemd/system/ /etc/systemd/system/
rsync -av etc/ssh/ /etc/ssh/
cp etc/hosts /etc/hosts
cp etc/hostname /etc/hostname

# AX42 only:
cp var/lib/gcg-postgres-data/postgresql.conf /var/lib/gcg-postgres-data/postgresql.conf
cp var/lib/gcg-postgres-data/pg_hba.conf /var/lib/gcg-postgres-data/pg_hba.conf

xargs -a etc/dpkg.selections.txt apt-get install -y || true
nft -f etc/nftables.ruleset.txt 2>/dev/null || iptables-restore < etc/iptables.ruleset.txt
```

### 5. Restore Borg snapshot (~45 min)
```bash
export BORG_PASSPHRASE="(from 1Password)"
export BORG_RSH="ssh -i /root/.ssh/storagebox_borg_appendonly -p 23"
borg list ssh://u547533@u547533.your-storagebox.de/./gcg-borg-2026-05-18 --short
ARCHIVE=gcg-ax102-YYYYMMDDTHHMMSS   # most recent BEFORE incident discovery
cd / && borg extract ssh://u547533@u547533.your-storagebox.de/./gcg-borg-2026-05-18::$ARCHIVE opt/gcg etc/credstore.encrypted etc/systemd/system
```

### 6. Reconstruct openclaw.json files (~10 min)
Until refactored to env-var references, each `/opt/gcg/openclaw-<agent>/openclaw.json` must be manually rebuilt from 1Password. Template per agent is in the Borg snapshot (post-restore). Replace key fields:
- `auth.api_key` (Anthropic, OpenAI, etc.) → pull from 1Password "GCG Anthropic - <agent>" items
- Per-agent Perplexity/xAI keys → 1Password "GCG <provider> - shared" items
The bootstrap script `/opt/gcg/shared/bin/gcg-secret-bootstrap.sh` already pulls runtime env from 1Password — the openclaw.json refactor will eliminate this manual step.

### 7. Restore Postgres (AX42 only, ~30 min)
```bash
mkdir -p /var/lib/gcg-postgres-data
docker compose -f /opt/gcg/docker-compose.yml up -d postgres
until docker inspect gcg-postgres --format "{{.State.Health.Status}}" | grep -q healthy; do sleep 2; done

scp -i /root/.ssh/storagebox_borg -P 23 u547533@u547533.your-storagebox.de:backups/dumps/daily/gcg_intelligence_LATEST.dump /tmp/
docker exec gcg-postgres dropdb -U gcg_admin --if-exists gcg_intelligence
docker exec gcg-postgres createdb -U gcg_admin gcg_intelligence
docker cp /tmp/gcg_intelligence_LATEST.dump gcg-postgres:/tmp/
docker exec gcg-postgres pg_restore -U gcg_admin -d gcg_intelligence -Fc /tmp/gcg_intelligence_LATEST.dump
```
Point-in-time: if dump is older than incident, WAL-replay from `backups/wal-archive-v2/`.

### 8. Bring up the fleet (~15 min)
```bash
systemctl daemon-reload
systemctl enable --now openclaw-nik.service    # test on Nik first
sleep 30 && fleet status nik

# Then groups of 5, NEVER all 29 in parallel (OOM cascade)
for agent in daen talos marcus mnemosyne vulcan; do
  systemctl enable --now openclaw-$agent.service
  sleep 10
done
```

### 9. Verify (~10 min)
```bash
systemctl list-units "openclaw-*" --state=running --no-pager | wc -l   # should be 29
docker exec gcg-postgres pg_isready -U gcg_admin -d gcg_intelligence
docker exec gcg-postgres psql -U gcg_admin -d postgres -tA -c "SHOW archive_mode;"   # on
systemctl status gcg-wal-ship.timer gcg-pg-dump.timer gcg-borg-backup.timer
```

## Scenario 2 — AX102 only nuked, AX42 healthy
Skip step 7. AX42 keeps running. Restore only compute.

## Scenario 3 — AX42 only nuked (DB gone)
```bash
systemctl stop "openclaw-*"   # agents crash-loop without DB
# Restore AX42 per steps 1-2, 4 (postgres-relevant), 7
systemctl start "openclaw-*"
```

## Scenario 4 — Borg archive recovery (soft-delete)
Append-only mode means "deletes" are soft. Data is still in segments. Use `borg debug get-obj` from Mac Mini (unrestricted key) to retrieve specific chunks. Full procedure: https://borgbackup.readthedocs.io/en/stable/internals/security.html

## Anti-restore: when NOT to restore from a snapshot
If dwell time of compromise was N days, any backup younger than N days might contain rootkit. Walk back to immutable baseline tag (`baseline-2026-05-18` — TODO: create). Diff candidate against baseline before trusting.

## RTO breakdown

| Step | Time |
|---|---|
| 1. Provision | 30 min |
| 2. Host key | 10 min |
| 3. Clone repo | 5 min |
| 4. Replay configs | 20 min |
| 5. Borg extract | 45 min |
| 6. openclaw.json rebuild | 10 min |
| 7. Postgres restore | 30 min |
| 8. Fleet start | 15 min |
| 9. Verify | 10 min |
| **Total** | **~3h** |

## Drill schedule
- Quarterly: full end-to-end on scratch Hetzner cloud VM (different account, destroyed after 1h)
- Monthly: lighter — Postgres restore from latest dump
- After every config change: verify next gcg-infra-snapshot landed on GitHub
- Drill logs → `runbook/drill-reports/YYYY-MM-DD.md`

## ACTIVE INCIDENT — what to do FIRST
1. **DO NOT** wipe AX102/AX42 immediately — forensics matter.
2. Isolate via Hetzner Robot → block all inbound except your home IP.
3. Snapshot disk via Hetzner rescue mode for later forensics.
4. Verify Borg + dumps + WAL still intact on storagebox AND Mac Mini.
5. THEN provision new servers and follow this runbook on FRESH hardware.
6. **Never** restore onto the compromised hardware.

## Open TODOs
- [ ] **URGENT:** Refactor `openclaw.json` files to use `${ENV_VAR}` references instead of plaintext API keys. 9 agents currently have leaked keys (see infra-repo .gitignore).
- [ ] **URGENT:** Rotate exposed keys: Anthropic (hector/phil/tom), Perplexity (argus/chiron/goku/jc/mnemosyne), xAI (argus/jc), 1Password SA token (gcg-google-broker.service).
- [ ] Sanitize Cloudflared token out of `/opt/gcg/docker-compose.yml` (currently plaintext).
- [ ] Create `baseline-2026-05-18` tag in Borg as immutable known-good reference.
- [ ] Schedule first quarterly DR drill.
- [ ] Onboard Sentinel for log-shipping and audit pulls.
- [ ] Onboard Mac Mini for pull-replica + prune.
- [ ] GPG-sign this file once GPG identity is set up.
