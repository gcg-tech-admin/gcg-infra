# gcg-infra

Infrastructure-as-code for GCG fleet (29 OpenClaw agents on Hetzner AX102 + Postgres on AX42).

## Layout
- `hosts/ax102/` — AX102 configs (systemd units, openclaw.json per agent, shared scripts, FLEET.yaml)
- `hosts/ax42/` — AX42 configs (docker-compose, postgres conf, systemd units)
- `runbook/` — disaster recovery procedures
- `dns/` — exported DNS zone records (populated by Sentinel)

## Auto-snapshot
- AX102 commits daily via `gcg-infra-snapshot.timer` at 04:00 Dubai
- AX42 commits daily via same mechanism at 04:15 Dubai
- Both push to `origin main` (private GitHub repo)

## What is committed
- `openclaw-*/openclaw.json` (secrets are env-injected, configs are safe)
- `/etc/systemd/system/openclaw-*.{service,d}` and `/etc/systemd/system/gcg-*.{service,timer}`
- `/etc/credstore.encrypted/*` (encrypted blobs, opaque — defense in depth)
- `/opt/gcg/shared/bin/*`, `/opt/gcg/shared/FLEET.yaml`, `FLEET_INDEX.md`
- `/etc/ssh/sshd_config`
- `/etc/postfix/` is NOT committed (purged)

## What is NEVER committed
- Plaintext API keys, OAuth bundles, .env files, runtime.env
- SSH private keys
- Agent workspaces (too large, often contains generated media)
- Docker volumes data (separate Borg backup handles state)
- Cloudflared token (TODO: move out of docker-compose.yml plaintext)
