#!/bin/bash
# Get Asana PAT for agent
AGENT=${1:-daen}
python3 << PYEOF
import json
import sys
from pathlib import Path
from cryptography.fernet import Fernet

vault_key = Path('/opt/gcg/shared/secrets/.vault_key')
vault_path = Path('/opt/gcg/shared/secrets/asana.enc')

key = vault_key.read_bytes()
fernet = Fernet(key)

blob = vault_path.read_bytes()
data = json.loads(fernet.decrypt(blob))

pat = data.get('pat:$AGENT', '')
if pat:
    print(pat)
else:
    print(f'ERROR: No PAT found for $AGENT', file=sys.stderr)
    sys.exit(1)
PYEOF
