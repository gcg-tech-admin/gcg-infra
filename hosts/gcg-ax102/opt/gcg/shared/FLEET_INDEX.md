# FLEET_INDEX.md # Boot map for all GCG agents. Load on startup. Fetch only what you need. # Last updated: 2026-05-20 | Owner: Daen 

---


## UPDATE LOG — 2026-05-23

### DeepSeek Provider Block Fix (fleet-wide)
27/29 agents were missing the `deepseek` provider block in `models.providers`, causing all `deepseek/deepseek-v4-pro:high` (and `deepseek-v4-flash`) calls to fail with `FailoverError: Unknown model` and silently fall back to kimi-k2.6. Fixed fleet-wide 2026-05-23. Provider block added to all affected agents: `baseUrl: https://api.deepseek.com/v1`, `apiKey: env:DEEPSEEK_API_KEY`, `api: openai-completions`. talos and vulcan already had the block (2/29 exceptions).

## UPDATE LOG — 2026-05-23 (correction detector timer fix)

### Correction detector timer — systemd OnUnitActiveSec trap resolved
`gcg-correction-detector.service` existed without a `.timer` for months — correction scan never fired. Fixed 2026-05-23: `gcg-correction-detector.timer` installed with `OnCalendar=hourly`. Root cause was a systemd scheduling trap: `oneshot` service + `RemainAfterExit=yes` + timer `OnUnitActiveSec` = timer never re-fires after first activation. **See Ops Gotchas §OG-1 below.**
Canonical: Memory CANONICAL §19 (Correction Detector).

## UPDATE LOG — 2026-05-21 (doc + memory ontologies)

### Canonical taxonomies are now BINDING fleet-wide

- **Memory ontology v2.1** — how memory rows in `memories` are typed/tagged/scoped. Canonical: `docs/architecture/MEMORY_ONTOLOGY_CANONICAL.md`. Four source_types: fact/lesson/preference/decision. Tags JSONB (2-4 lowercase hyphenated). Migrator: `gcg_tools/batch_ontology_migrator.py`.
- **Doc ontology v1.1** — how docs are organized in `/opt/gcg/shared/docs/`. Canonical: `docs/architecture/DOC_ONTOLOGY_CANONICAL.md`. Four buckets: architecture/reference/handoffs/reviews. Tag vocab shared with memory ontology §6.2.
- **Tooling:** `/opt/gcg/shared/bin/gcg-doc-sort` (classifier + mover, daily dry-run cron), `/opt/gcg/shared/bin/gcg-doc-tag` (frontmatter backfill).
- **APPROVED_TAXONOMY** in `knowledge_maintenance.py` now includes council/handoffs/reviews — no more violation alerts on those dirs.

When creating a doc, consult DOC_ONTOLOGY_CANONICAL.md for bucket + naming. When writing a memory, consult MEMORY_ONTOLOGY_CANONICAL.md for source_type + tags.

## UPDATE LOG — 2026-05-20 (afternoon)

### Google access pipeline rebuilt (Cowork)
- 1Password item canonicalized: **GCG GOOGLE BROKER** (UUID `sth5aujsjwj54yu2uvnr6klexa`) — single shared DWD SA.
- Bootstrap (`/opt/gcg/shared/bin/gcg-secret-bootstrap.sh`) now writes Google SA + Anthropic api_key into tmpfs `/run/openclaw-<agent>/` on every restart. Zero plaintext on persistent disk.
- `gcg_google_v2/_auth.py` patched to prefer `GOOGLE_APPLICATION_CREDENTIALS` env over PG cache / op CLI.
- 26 v2 agents rolled out + restarted. All loading fresh key `19e05d85a38d`.
- Audit log column-level UPDATE grant added for every `gcg_<agent>` role.
- Stale `google_sa_cache` PG table purged (was holding burned key with 2029 expiry).
- Canonical doc: `/opt/gcg/shared/docs/reference/google-broker-usage.md` (rewritten).

## UPDATE LOG — 2026-05-20

### DB Access Documented (Talos)
Canonical DB access section added below. All agents reach PostgreSQL via VLAN (AX102→AX42). Per-agent `.pwd` credentials in `/opt/gcg/shared/credentials/db/`. Admin vault initialized at `/opt/gcg/shared/secrets/`.

---

## UPDATE LOG — 2026-05-04
 
### Heartbeat Model Change (fleet-wide) Default heartbeat model changed from `google/gemini-3.1-flash-lite-preview` to `deepseek/deepseek-v4-flash`. Flash Lite was causing fleet-wide timeouts; DeepSeek V4 Flash is reliable and cost-effective ($0.14/$0.28 per M tokens).
 
### DeepSeek V4 Pro Unbanned `deepseek/deepseek-v4-pro` removed from banned list. Use with `thinking=on` for reliable multi-turn conversations.
 
### US Agents → Claude Sonnet 4.6 (Bedrock) 6 agents switched to `amazon-bedrock/us.anthropic.claude-sonnet-4-6` as primary model: bob, leon, hector, tom, phil, algaib.
 
### Memory Limits (systemd overrides, fleet-wide) Applied to all 28 agents: - **Tier 1 (6G):** daen, talos, marcus, mnemosyne, vulcan, goku, argus, varys, nik - **Tier 2 (4G):** all others
 
### OOM Crash Recovery All agents enabled for auto-start after OOM kills.
 
### Flash Lite Heartbeat Timeout Fix Applied fleet-wide — replaced timed-out Flash Lite heartbeat calls with DeepSeek V4 Flash. 

---

## THE FLEET Who's who, ports, models, Telegram bots, human assignments: → `/opt/gcg/shared/config/fleet-roster.yaml`
 Infrastructure (server IPs, services, vSwitch): → `/opt/gcg/shared/config/fleet_topology.yaml` Human contacts, roles, email, Telegram: → `/opt/gcg/shared/docs/fleet/HUMANS.md` Google API + DWD broker reference: → `/opt/gcg/shared/docs/reference/google-broker-usage.md`
 Glossary of fleet terms and system names: → `/opt/gcg/shared/docs/fleet/MERIDIAN_GLOSSARY.md` 

---

## MODEL POLICY Which model for which task (canonical, Peter-approved): → `/opt/gcg/shared/docs/architecture/FLEET_MODEL_DECISIONS.md` → `/opt/gcg/shared/docs/sops/MODEL_ROUTING_MATRIX.md` → `/opt/gcg/shared/config/model_policy.yaml` → `/opt/gcg/shared/config/model_pricing.yaml` → `/opt/gcg/shared/config/subagent_policy.yaml` 
### ⚠️ Subagent Dispatch Reliability (2026-05-03) Gemini models are CURRENTLY UNRELIABLE for `sessions_spawn` execution work: - `google/gemini-3.1-pro-preview` — silent 120s timeouts, session aborts before fallback engages - `google/gemini-3.1-flash-lite-preview` — hallucinates completions (claims done, executes zero tools)
 **Use for execution subagents:** `deepseek/deepseek-v4-flash` ($0.14/$0.28 per M, 1M ctx) for deterministic file/config tasks. `moonshot/kimi-k2.6` ($0.60/$2.50 per M) for tool-use heavy or judgment tasks. See MODEL_ROUTING_MATRIX.md for full failure log + cost table. 

---

## DATABASE ACCESS (PostgreSQL 15 + pgvector on AX42)

**Canonical module:** `/opt/gcg/shared/gcg_tools/db_config.py` — single source of truth.

### Agent connections (RLS-scoped) — use this 99% of the time:
```python
import sys; sys.path.insert(0, '/opt/gcg/shared/gcg_tools')
from db_config import get_connection
conn = get_connection(agent_name='<agent>')
```
- Auth: `/opt/gcg/shared/credentials/db/gcg_<agent>.pwd`
- User: `gcg_<agent>` (RLS-enforced, per-agent row-level security)
- Transport: `localhost:5432` → PgBouncer → AX42 over VLAN (10.0.0.1↔10.0.0.2)
- Database: `gcg_intelligence`

### Admin connections (bypasses RLS — migrations/platform only):
```python
conn = get_connection(admin=True)
```
- Auth: Fernet vault at `/opt/gcg/shared/secrets/db.enc` (key: `.vault_key`)
- Admin role restricted to AX42 localhost by design
- To re-init vault: `python3 /opt/gcg/shared/secrets/vault.py init`

### Credential files:
- Per-agent: `/opt/gcg/shared/credentials/db/gcg_<agent>.pwd` (plaintext, chmod 600)
- Admin vault: `/opt/gcg/shared/secrets/db.enc` + `/opt/gcg/shared/secrets/.vault_key`

---

## MEMORY & KNOWLEDGE How memory works (pgvector, scopes, write rules): → `/opt/gcg/shared/docs/architecture/MEMORY_CANONICAL.md` How to query the knowledge base: → `/opt/gcg/shared/skills/gcg-kb-query/SKILL.md` How to write structured memories: → `/opt/gcg/shared/docs/sops/STRUCTURED_MEMORY_SOP.md` What's indexed in pgvector: → `/opt/gcg/shared/docs/KNOWLEDGE_INDEX.md` 
### ⚠️ Embedding Provider Gotcha (READ BEFORE TOUCHING MEMORY CONFIG) OpenClaw uses inconsistent provider naming and the schema does n

---

## OPS GOTCHAS

Known systemd/infra traps that have caused real incidents. Read before touching timers or services.

### OG-1 — systemd: oneshot + RemainAfterExit + OnUnitActiveSec = timer never re-fires

**Trap:** A `Type=oneshot` service with `RemainAfterExit=yes` transitions to `active (exited)` after its first run and stays there. A timer using `OnUnitActiveSec=` only re-fires when the unit transitions from inactive → active. Since the unit stays `active (exited)` forever, the timer never re-fires.

**Symptom:** Service runs once (at boot or manual start), never again. `systemctl status` shows "active (exited)" indefinitely. No error, no alert.

**Fix:** Use `OnCalendar=` (e.g. `OnCalendar=hourly`) in the timer, NOT `OnUnitActiveSec=`. Remove `RemainAfterExit=yes` from the service if the timer needs periodic execution.

**Origin:** `gcg-correction-detector.timer` missing for months; correction scanning silently inactive 2026-05-23.
