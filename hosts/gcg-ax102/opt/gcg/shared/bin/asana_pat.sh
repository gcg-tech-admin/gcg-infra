#!/usr/bin/env bash
# asana_pat.sh — Decrypt and echo an agent's current Asana PAT.
#
# Usage:   asana_pat.sh <agent>
# Example: asana_pat.sh talos
#
# Reads /opt/gcg/shared/secrets/asana.enc (Fernet-encrypted JSON) using the
# canonical .vault_key file. Echoes the PAT to stdout, nothing else.
# Exits non-zero with a stderr message on any error.

set -euo pipefail

AGENT="${1:-}"
if [[ -z "$AGENT" ]]; then
    echo "asana_pat.sh: usage: $0 <agent>" >&2
    exit 2
fi

VAULT="/opt/gcg/shared/secrets/asana.enc"
KEY="/opt/gcg/shared/secrets/.vault_key"
PY="/opt/gcg/shared/venv/bin/python3"

[[ -r "$VAULT" ]] || { echo "asana_pat.sh: vault unreadable: $VAULT" >&2; exit 1; }
[[ -r "$KEY"   ]] || { echo "asana_pat.sh: key unreadable: $KEY"     >&2; exit 1; }
[[ -x "$PY"    ]] || { echo "asana_pat.sh: python missing: $PY"      >&2; exit 1; }

exec "$PY" - "$AGENT" <<'PYEOF'
import json, sys
from pathlib import Path
from cryptography.fernet import Fernet
agent = sys.argv[1]
data = json.loads(
    Fernet(Path("/opt/gcg/shared/secrets/.vault_key").read_bytes())
    .decrypt(Path("/opt/gcg/shared/secrets/asana.enc").read_bytes())
)
pat = data.get(f"pat:{agent}")
if not pat:
    sys.stderr.write(f"asana_pat.sh: no PAT for {agent} in vault\n")
    sys.exit(1)
sys.stdout.write(pat)
PYEOF
