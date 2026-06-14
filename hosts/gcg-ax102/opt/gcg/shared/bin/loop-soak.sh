#!/bin/bash
# loop-soak.sh — Milestone Zero standing gate for the fleet execution loop.
# Sends N diagnostic tasks across fleet agents and measures time-to-terminal.
# GREEN = >=96% reach terminal (done/cancelled/failed) within DEADLINE, 0 stuck.
# A task reaching terminal means: poke → agent turn → fleet done. That IS the loop.
#
# Usage:  loop-soak.sh
# Env:    SOAK_AGENTS="talos socrates ..."  SOAK_PER=3  SOAK_DEADLINE=300
set -o pipefail

PWD_FILE="/opt/gcg/shared/credentials/db/gcg_fleet.pwd"
PSQL() { PGPASSWORD=$(tr -d '\r\n' <"$PWD_FILE") psql -h 10.0.0.2 -p 5432 -U gcg_fleet -d gcg_intelligence -tA "$@" 2>/dev/null; }
FLEET=/opt/gcg/shared/bin/fleet

read -r -a AGENTS <<< "${SOAK_AGENTS:-talos socrates nemesis cassandra confucius wonhoo vulcan}"
PER=${SOAK_PER:-3}
DEADLINE=${SOAK_DEADLINE:-300}
TOKEN="soak-$(date -u +%s)"

declare -A IDS
echo "[$TOKEN] sending $(( ${#AGENTS[@]} * PER )) tasks across ${#AGENTS[@]} agents (deadline ${DEADLINE}s)..."
for a in "${AGENTS[@]}"; do
  for i in $(seq 1 "$PER"); do
    out=$($FLEET send --from daen "$a" "SOAK TEST [$TOKEN #$i] — diagnostic only, NO action required. Close it immediately: fleet done <this message id>. Do not reply, do not analyze." 2>/dev/null)
    id=$(echo "$out" | grep -oE '^[0-9]+' | head -1)
    [[ "$id" =~ ^[0-9]+$ ]] && IDS[$id]=$a
  done
done
total=${#IDS[@]}
[ "$total" -eq 0 ] && { echo ">>> RED: no tasks sent (fleet send failed)"; exit 1; }
idlist=$(IFS=,; echo "${!IDS[*]}")
echo "[$TOKEN] sent $total tasks. polling up to ${DEADLINE}s..."

start=$(date +%s)
declare -A TERM_AT
while :; do
  now=$(date +%s); el=$((now - start))
  for id in $(PSQL -c "SELECT id FROM agent_messages WHERE id IN ($idlist) AND status IN ('done','cancelled','failed')"); do
    [ -z "${TERM_AT[$id]:-}" ] && TERM_AT[$id]=$el
  done
  [ "${#TERM_AT[@]}" -ge "$total" ] && break
  [ "$el" -ge "$DEADLINE" ] && break
  sleep 15
done

termcount=${#TERM_AT[@]}
stuck=$((total - termcount))
slowest=0; for id in "${!TERM_AT[@]}"; do [ "${TERM_AT[$id]}" -gt "$slowest" ] && slowest=${TERM_AT[$id]}; done
pct=$(( termcount * 100 / total ))
echo "=== [$TOKEN] RESULTS ==="
echo "sent=$total terminal=$termcount stuck=$stuck slowest_terminal=${slowest}s deadline=${DEADLINE}s"
if [ "$stuck" -gt 0 ]; then
  echo "STUCK (not terminal):"
  PSQL -c "SELECT '  #'||id||' '||recipient||' '||status||' (sent '||round(extract(epoch from (now()-issued_at)))||'s ago)' FROM agent_messages WHERE id IN ($idlist) AND status NOT IN ('done','cancelled','failed') ORDER BY recipient"
fi
if [ "$termcount" -ge $(( (total * 96 + 99) / 100 )) ] && [ "$stuck" -eq 0 ]; then
  echo ">>> GREEN — $pct% terminal within ${DEADLINE}s, 0 stuck. Loop holds."
  exit 0
else
  echo ">>> RED — $pct% terminal, $stuck stuck. Loop NOT solid."
  exit 1
fi
