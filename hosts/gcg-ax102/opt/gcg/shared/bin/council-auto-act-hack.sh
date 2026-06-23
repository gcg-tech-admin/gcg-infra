#!/bin/bash
# council-auto-act-hack.sh — TEMPORARY HACK for auto-acting on council revision requests
# Runs every 5 min via cron. Checks daen's inbox for council revision messages.
# If found, sends Telegram alert to Peter so daen can auto-act.
# TODO: Replace with proper Gateway system-event injection.

set -euo pipefail

AGENT="daen"
TELEGRAM_CHAT="418059105"
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
DB_HOST="10.0.0.2"
DB_USER="gcg_admin"
DB_NAME="gcg_intelligence"

# Check for council revision messages in daen's inbox
# Use a simpler query that doesn't require complex escaping
REVISION_MSGS=$(ssh -o ConnectTimeout=5 root@"$DB_HOST" "docker exec gcg-postgres psql -U $DB_USER -d $DB_NAME -t -c \"SELECT COUNT(*) FROM agent_messages WHERE recipient = '$AGENT' AND status IN ('pending','delivered') AND (payload->>'text' ILIKE '%council%' OR payload->>'text' ILIKE '%revise%' OR payload->>'text' ILIKE '%verdict%' OR sender = 'council_tick');\"" 2>/dev/null || echo "0")

# Trim whitespace
REVISION_MSGS=$(echo "$REVISION_MSGS" | tr -d '[:space:]')

if [ "$REVISION_MSGS" != "0" ] && [ -n "$REVISION_MSGS" ]; then
    # Send Telegram alert
    if [ -n "$TELEGRAM_BOT_TOKEN" ]; then
        curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d "chat_id=${TELEGRAM_CHAT}" \
            -d "text=[COUNCIL REVISION NEEDED] Council requires plan revision. $REVISION_MSGS message(s) waiting." \
            >/dev/null 2>&1 || true
    fi
    
    # Log for debugging
    echo "$(date -u +%FT%TZ) Council revision messages found for $AGENT: $REVISION_MSGS" >&2
fi
