# Disaster Recovery Runbook

If both AX102 and AX42 are nuked, reach a working fleet from this repo + storagebox in ~4 hours.

## Pre-conditions you need
1. Storagebox SSH key (held in 1Password or on Mac Mini)
2. systemd-creds host key from 1Password
3. Fresh Hetzner provision of AX102 + AX42 (Ubuntu 24.04)

## Sequence
1. Provision fresh hardware (Hetzner Robot, Ubuntu 24.04)
2. Restore systemd-creds host key to `/var/lib/systemd/credential.secret`
3. Clone this repo to `/opt/gcg/infra-repo`
4. Run `sudo /opt/gcg/infra-repo/runbook/restore-host.sh ax102` (TODO: write this)
5. Restore Borg snapshot: `borg extract ssh://...//gcg-borg-2026-05-18::<archive>`
6. Restore Postgres from `backups/dumps/daily/gcg_intelligence_*.dump`
7. WAL-replay from `backups/wal-archive-v2/` if needed
8. systemctl enable + start all openclaw-*.service
9. Verify with `fleet status`

## RTO target
4 hours from clean provision to all 29 agents responsive.

## Last tested
(TODO: schedule quarterly DR drill)
