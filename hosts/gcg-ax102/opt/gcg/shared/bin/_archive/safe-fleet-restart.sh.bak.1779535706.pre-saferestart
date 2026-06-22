#!/bin/bash
# safe-fleet-restart.sh — rolling restart of openclaw-* services with parallelism + memory guard
# Caps at 5 concurrent restarts, 30s gap between waves, aborts wave if memory drops below threshold.
# Created 2026-05-18 after rapid fleet restart hung AX102 via OOM cascade.

set -euo pipefail

CONCURRENCY="${CONCURRENCY:-5}"
GAP_SEC="${GAP_SEC:-30}"
MIN_FREE_MB="${MIN_FREE_MB:-8000}"   # abort wave if free mem drops below this
TIMEOUT_PER_AGENT="${TIMEOUT_PER_AGENT:-60}"

# Critical 6 — restart last, one at a time, to minimize blast radius
CRITICAL="daen talos marcus mnemosyne vulcan nik"

mem_free_mb() {
  awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo
}

restart_one() {
  local a="$1"
  echo "  [$(date -u +%H:%M:%S)] restart openclaw-$a"
  systemctl reset-failed "openclaw-$a" 2>/dev/null || true
  timeout "$TIMEOUT_PER_AGENT" systemctl restart "openclaw-$a"
  local s
  s=$(systemctl is-active "openclaw-$a")
  echo "  [$(date -u +%H:%M:%S)] openclaw-$a: $s"
}

restart_wave() {
  local agents=("$@")
  echo "[wave] ${agents[*]}  (free=${1}MB)"
  for a in "${agents[@]}"; do
    restart_one "$a" &
  done
  wait
  local free
  free=$(mem_free_mb)
  if [ "$free" -lt "$MIN_FREE_MB" ]; then
    echo "[WARN] free memory ${free}MB < ${MIN_FREE_MB}MB — pausing 60s extra before next wave"
    sleep 60
  fi
}

ALL=$(ls -d /opt/gcg/openclaw-*/ | awk -F/ '{print $(NF-1)}' | sed 's/openclaw-//')
NON_CRIT=$(for a in $ALL; do c=0; for x in $CRITICAL; do [ "$a" = "$x" ] && c=1; done; [ $c -eq 0 ] && echo "$a"; done)

echo "=== safe-fleet-restart ==="
echo "  total agents: $(echo "$ALL" | wc -w)"
echo "  critical (restart last, sequential): $CRITICAL"
echo "  non-critical (rolling, $CONCURRENCY at a time): $(echo "$NON_CRIT" | wc -w)"
echo "  free memory now: $(mem_free_mb)MB"
echo

# Roll non-critical agents in waves of $CONCURRENCY
WAVE=()
for a in $NON_CRIT; do
  WAVE+=("$a")
  if [ "${#WAVE[@]}" -ge "$CONCURRENCY" ]; then
    restart_wave "${WAVE[@]}"
    WAVE=()
    sleep "$GAP_SEC"
  fi
done
# flush
[ "${#WAVE[@]}" -gt 0 ] && restart_wave "${WAVE[@]}"

# Critical agents — one at a time
echo
echo "=== critical agents — one at a time, 10s gap ==="
for a in $CRITICAL; do
  restart_one "$a"
  sleep 10
done

echo
echo "=== final state ==="
systemctl list-units 'openclaw-*.service' --state=active --no-legend | wc -l
echo "non-active:"
for a in $ALL; do
  s=$(systemctl is-active "openclaw-$a")
  [ "$s" != "active" ] && echo "  $a $s"
done
echo "done. free memory: $(mem_free_mb)MB"
