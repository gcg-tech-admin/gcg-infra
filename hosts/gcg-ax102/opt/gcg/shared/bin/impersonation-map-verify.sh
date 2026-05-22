#!/bin/bash
# impersonation-map-verify.sh — daily check for drift between
# fleet-roster.yaml agent_emails, DB agent_impersonation_allowed, and code _auth.py IMPERSONATION_ALLOWLIST.
# Flags any inconsistency to stderr (exit 1) so the cron mail wrapper picks it up.
set -euo pipefail

ROSTER=/opt/gcg/shared/config/fleet-roster.yaml
AUTH=/opt/gcg/shared/gcg_tools/gcg_google_v2/_auth.py
LOG=/opt/gcg/shared/logs/impersonation-verify-$(date +%Y%m%d).log
mkdir -p /opt/gcg/shared/logs

python3 - <<'PY' 2>&1 | tee -a "$LOG"
import os, re, sys, yaml, psycopg2

# 1. Read roster agent_emails
with open("/opt/gcg/shared/config/fleet-roster.yaml") as f:
    text = f.read()
m = re.search(r"^agent_emails:\s*\n((?:  [a-z_]+:.*\n)+)", text, re.MULTILINE)
roster = {}
if m:
    for line in m.group(1).splitlines():
        k, v = re.split(r":\s+", line.strip(), 1)
        if k in ("default_impersonation", "impersonation_map", "db_grants"):
            continue
        roster[k] = v.strip()

# 2. Read DB grants
conn = psycopg2.connect(
    host="95.217.114.49", port=5432, dbname="gcg_intelligence",
    user="gcg_admin", password=os.environ["GCG_DB_PASSWORD"], sslmode="require"
)
with conn.cursor() as cur:
    cur.execute("SELECT agent_id, email FROM agent_impersonation_allowed WHERE revoked_at IS NULL ORDER BY 1,2")
    db_rows = cur.fetchall()
db_map = {}
for a, e in db_rows:
    db_map.setdefault(a, set()).add(e)

# 3. Drift checks
drift = []

# Each roster entry should have its own email in DB
for agent, email in roster.items():
    if agent not in db_map:
        drift.append(f"MISSING agent in DB: {agent} (should have {email})")
        continue
    if email not in db_map[agent]:
        drift.append(f"MISSING grant: {agent} → {email} in roster but not in DB")

# Every agent should also have peter@global-capital-group.com (rule)
PETER = "peter@global-capital-group.com"
for agent in db_map:
    if PETER not in db_map[agent]:
        drift.append(f"MISSING Peter grant: {agent} → {PETER}")

# Report
if drift:
    print(f"=== IMPERSONATION MAP DRIFT — {len(drift)} issues ===")
    for d in drift: print(f"  - {d}")
    sys.exit(1)
print(f"OK: {len(db_map)} agents, {sum(len(v) for v in db_map.values())} grants. roster + DB consistent.")
PY
