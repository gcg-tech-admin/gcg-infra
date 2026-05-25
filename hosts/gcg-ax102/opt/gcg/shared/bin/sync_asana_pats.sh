#!/bin/bash
# sync_asana_pats.sh — Sync all agent PATs from vault to plaintext .txt files
# Run after rotation or when .txt files are detected stale/missing.
# Usage: /opt/gcg/shared/bin/sync_asana_pats.sh [agent ...]

set -euo pipefail

VAULT="/opt/gcg/shared/secrets/asana.enc"
VAULT_KEY="/opt/gcg/shared/secrets/.vault_key"
CREDS_DIR="/opt/gcg/shared/credentials"

if [ $# -gt 0 ]; then
    AGENTS=("$@")
else
    # Default: all agents with PATs in vault
    AGENTS=(nik daen vulcan viktor varys kenji marcus anna talos)
fi

for agent in "${AGENTS[@]}"; do
    pat=$(python3 << PYEOF
import json, sys
from pathlib import Path
from cryptography.fernet import Fernet
key = Path("$VAULT_KEY").read_bytes()
data = json.loads(Fernet(key).decrypt(Path("$VAULT").read_bytes()))
pat = data.get("pat:$agent", "")
if pat:
    print(pat)
else:
    sys.exit(1)
PYEOF
    )
    if [ -z "$pat" ]; then
        echo "  $agent: SKIP — no PAT in vault"
        continue
    fi
    txt_path="$CREDS_DIR/asana_pat_${agent}.txt"
    echo "$pat" > "$txt_path"
    chmod 600 "$txt_path"
    echo "  $agent: ✓ synced"
done

echo "Done."
