#!/bin/bash
# Deterministic fleet build driver (replaces the LLM-judgment cron that spammed).
# Compares per-task STATUS ONLY (ignores volatile blocked_reason text) and pings
# daen ONLY on an actual status transition. No LLM. Routes everything to daen;
# daen relays genuine milestones to Peter. Run by system cron every 15 min.
set -uo pipefail

IDS="154981,155018,155081,155104,153594,153595,153596,153597,155304,155449"
STATE=/tmp/fleet_driver_state2
PWF=/opt/gcg/shared/credentials/db/gcg_fleet.pwd
FLEET=/usr/local/bin/fleet

PW=$(tr -d '\r\n' < "$PWF")
CUR=$(PGPASSWORD="$PW" psql "host=10.0.0.2 port=5432 user=gcg_fleet dbname=gcg_intelligence sslmode=require" \
  -tA -F'|' -c "SELECT id,status FROM agent_messages WHERE id IN ($IDS) ORDER BY id;" 2>/dev/null)
[ -z "$CUR" ] && exit 0   # DB unreachable: do nothing rather than misfire

touch "$STATE"
while IFS='|' read -r id st; do
  [ -z "$id" ] && continue
  prev=$(grep "^$id|" "$STATE" | head -1 | cut -d'|' -f2)
  [ "$st" = "$prev" ] && continue          # STATUS unchanged -> silent (the dedup that was broken)
  case "$st" in
    done)           "$FLEET" send --from build-driver daen "✅ task $id reached done" >/dev/null 2>&1 ;;
    input-required) "$FLEET" send --from build-driver daen "🔎 task $id input-required — gated for daen review" >/dev/null 2>&1 ;;
    failed)         "$FLEET" send --from build-driver daen "⛔ task $id FAILED — needs re-dispatch" >/dev/null 2>&1 ;;
  esac
done <<< "$CUR"

printf '%s\n' "$CUR" > "$STATE"
