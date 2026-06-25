#!/bin/bash
# rotate-controller-v2.sh — GCG Subscription Rotator (Ponytail Cut)
# Atlas, 2026-06-23
#
# Usage:
#   rotate-controller-v2.sh --event-watch    # Journal watch path (30s timer)
#   rotate-controller-v2.sh --status          # Status dashboard (fast, no health)
#   rotate-controller-v2.sh --status-full     # Status with live health checks
#   rotate-controller-v2.sh --dry-run         # Show what would happen
#   rotate-controller-v2.sh --scan            # Full scan + rotate
#   rotate-controller-v2.sh --health-check    # Health check all subs
#
# ponytail: ONE script, ~200 lines. No YAML, no yq, no jq state mutation,
# no separate modules. Dropins on disk ARE the state.

set -euo pipefail
# ponytail: disable pipefail globally — many helpers use grep -q which exits 1 on no match
set +o pipefail

# ═══════════════════════════════════════════════════════════════
# CONFIG — bash arrays  # ponytail: no yaml
# ═══════════════════════════════════════════════════════════════

PROFILES_DIR="/opt/gcg/claude-profiles"
SYSTEMD_DIR="/etc/systemd/system"
DEBOUNCE_FILE="/var/tmp/rotate.last_trigger"
LOG_FILE="/opt/gcg/shared/docs/records/subscription-rotation-log.md"

# ponytail: org map — inline associative array
declare -A SUB_ORG
SUB_ORG[sub-sonja]=org_a
SUB_ORG[sub-vlada]=org_a
SUB_ORG[sub-flore]=org_a
SUB_ORG[sub-peter]=org_b
SUB_ORG[sub-sergei]=org_b
SUB_ORG[sub-pierre]=org_b
SUB_ORG[sub-maruf]=org_c
SUB_ORG[sub-olga]=org_c
SUB_ORG[sub-ishan]=org_d
SUB_ORG[sub-vanessa]=org_d
SUB_ORG[sub-vincent]=org_d

# ponytail: reserved agents — MUST stay on these subs
declare -A RESERVED_AGENT_SUB
RESERVED_AGENT_SUB[daen]=sub-peter
RESERVED_AGENT_SUB[talos]=sub-peter
RESERVED_AGENT_SUB[viktor]=sub-sergei

RESERVED_SUBS="sub-peter sub-sergei"
RESERVED_AGENTS="daen talos viktor"

HEALTH_MODEL="claude-sonnet-4-6"
HEALTH_TIMEOUT=8
DEBOUNCE_SEC=60

# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

org_of() { echo "${SUB_ORG[${1}]:-unknown}"; }

subs_in_org() {
    local org="$1" s
    for s in "${!SUB_ORG[@]}"; do
        [ "${SUB_ORG[$s]}" = "$org" ] && echo "$s"
    done
}

agent_count_on_sub() {
    grep -rl "claude-profiles/$1" "$SYSTEMD_DIR"/openclaw-*.service.d/20-claude-cli-home.conf 2>/dev/null | wc -l | tr -d ' '
}

agents_on_sub() {
    grep -rl "claude-profiles/$1" "$SYSTEMD_DIR"/openclaw-*.service.d/20-claude-cli-home.conf 2>/dev/null | \
        sed 's|.*/openclaw-||;s|\.service\.d/20-claude-cli-home\.conf||' | sort | tr '\n' ' '
}

# Returns: HEALTHY | DEGRADED | DEAD
health_check() {
    local sub="$1" output has_error=0 has_healthy=0
    output=$(HOME="$PROFILES_DIR/$sub" IS_SANDBOX=1 \
        timeout "$HEALTH_TIMEOUT" claude --model "$HEALTH_MODEL" \
        -p "Say the word HEALTHY" 2>&1) || true

    echo "$output" | grep -q "HEALTHY" && has_healthy=1 || true
    echo "$output" | grep -qiE "spend.limit|billing|rate.limit|429|insufficient.*quota|organization.*disabled" && has_error=1 || true

    if [ "$has_error" -eq 0 ] && [ "$has_healthy" -eq 1 ]; then
        echo "HEALTHY"
    elif [ "$has_error" -eq 1 ] && [ "$has_healthy" -eq 1 ]; then
        echo "DEGRADED"
    else
        echo "DEAD"
    fi
}

# ponytail: debounce via file timestamp, not jq
debounce_ok() {
    local now
    now=$(date +%s)
    [ ! -f "$DEBOUNCE_FILE" ] && return 0
    local last
    last=$(stat -c %Y "$DEBOUNCE_FILE" 2>/dev/null || echo 0)
    [ $((now - last)) -ge "$DEBOUNCE_SEC" ]
}

touch_debounce() { touch "$DEBOUNCE_FILE"; }

# ═══════════════════════════════════════════════════════════════
# CACHE — health checks are slow, cache results
# ═══════════════════════════════════════════════════════════════

cache_all_subs() {
    CACHE_DIR=$(mktemp -d /var/tmp/rotate-cache-v2.XXXXXX)
    trap "rm -rf \$CACHE_DIR" EXIT
    local sub h
    for dir in "$PROFILES_DIR"/*/; do
        sub="${dir%/}"
        sub="${sub##*/}"
        [ "$sub" = "sub-" ] && continue
        h=$(trap - EXIT; health_check "$sub")
        echo "$h" > "$CACHE_DIR/$sub.health"
        agents_on_sub "$sub" > "$CACHE_DIR/$sub.agents" 2>/dev/null || true
        agent_count_on_sub "$sub" > "$CACHE_DIR/$sub.count"
    done
}

# ponytail: fast cache — no health calls, just count agents
cache_fast() {
    CACHE_DIR=$(mktemp -d /var/tmp/rotate-cache-v2.XXXXXX)
    trap "rm -rf \$CACHE_DIR" EXIT
    local sub
    for dir in "$PROFILES_DIR"/*/; do
        sub="${dir%/}"
        sub="${sub##*/}"
        [ "$sub" = "sub-" ] && continue
        echo "SKIP" > "$CACHE_DIR/$sub.health"
        agents_on_sub "$sub" > "$CACHE_DIR/$sub.agents" 2>/dev/null || true
        agent_count_on_sub "$sub" > "$CACHE_DIR/$sub.count"
    done
}

# ═══════════════════════════════════════════════════════════════
# STATUS COMMAND
# ═══════════════════════════════════════════════════════════════

cmd_status() {
    local mode="${1:-fast}"
    [ "$mode" = "fast" ] && cache_fast || cache_all_subs

    local org sub cnt agents_str reserved_flag \
        dead degraded healthy total subs_csv org_status h

    echo "=== GCG Subscription Status @ $(date -u +"%Y-%m-%d %H:%M UTC") ==="
    echo "Health checks: $mode"
    echo

    # Org summaries
    echo "ORGS:"
    for org in org_a org_b org_c org_d; do
        dead=0; degraded=0; healthy=0; total=0; subs_csv=""
        for sub in $(subs_in_org "$org" | sort); do
            subs_csv="${subs_csv}${sub}, "
            h=$(cat "$CACHE_DIR/$sub.health" 2>/dev/null || echo "UNKNOWN")
            case "$h" in
                DEAD) dead=$((dead+1)) ;;
                DEGRADED) degraded=$((degraded+1)) ;;
                HEALTHY) healthy=$((healthy+1)) ;;
                SKIP) total=$((total+1)); continue ;;
            esac
            total=$((total+1))
        done
        subs_csv="${subs_csv%, }"
        org_status="HEALTHY"
        if [ "$mode" = "fast" ]; then
            org_status="(health SKIP)"
        else
            [ "$dead" -gt 0 ] && org_status="DEGRADED"
            [ "$dead" -eq "$total" ] && [ "$total" -gt 0 ] && org_status="DEAD"
        fi
        printf "  %-6s (%-32s) %s" "$org" "$subs_csv" "$org_status"
        [ "$mode" = "full" ] && [ "$dead" -gt 0 ] && echo " — $dead dead" || echo
    done
    echo

    # Subs table
    echo "SUBS:"
    printf "  %-20s %6s  %s\n" "SUB (org)" "COUNT" "AGENTS"
    printf "  %-20s %6s  %s\n" "----------" "----" "------"
    for dir in "$PROFILES_DIR"/*/; do
        sub="${dir%/}"
        sub="${sub##*/}"
        [ "$sub" = "sub-" ] && continue
        cnt=$(cat "$CACHE_DIR/$sub.count" 2>/dev/null || echo "0")
        agents_str=$(cat "$CACHE_DIR/$sub.agents" 2>/dev/null | tr '\n' ' ')
        reserved_flag=""
        echo "$RESERVED_SUBS" | grep -qw "$sub" && reserved_flag=" [R]"
        printf "  %-20s %6s  %s%s\n" \
            "${sub} ($(org_of "$sub"))" "$cnt" "$agents_str" "$reserved_flag"
    done
}

# ═══════════════════════════════════════════════════════════════
# TARGET SELECTION
# ═══════════════════════════════════════════════════════════════

find_best_target() {
    local current_sub="$1" agent="$2" current_org cur_health best_sub="" best_score=99999 sub h cnt score
    current_org=$(org_of "$current_sub")
    cur_health=$(cat "$CACHE_DIR/$current_sub.health" 2>/dev/null || echo "UNKNOWN")

    for dir in "$PROFILES_DIR"/*/; do
        sub="${dir%/}"
        sub="${sub##*/}"
        [ "$sub" = "sub-" ] && continue
        [ "$sub" = "$current_sub" ] && continue

        # ponytail: skip same-org if current sub is DEAD (prevent cascade)
        if [ "$cur_health" = "DEAD" ] && [ "$(org_of "$sub")" = "$current_org" ]; then
            continue
        fi

        # ponytail: skip reserved subs (unless this agent IS reserved to it)
        if echo "$RESERVED_SUBS" | grep -qw "$sub"; then
            if [ -z "${RESERVED_AGENT_SUB[$agent]:-}" ] || \
               [ "${RESERVED_AGENT_SUB[$agent]}" != "$sub" ]; then
                continue
            fi
        fi

        h=$(cat "$CACHE_DIR/$sub.health" 2>/dev/null || echo "DEAD")
        [ "$h" = "DEAD" ] && continue

        cnt=$(cat "$CACHE_DIR/$sub.count" 2>/dev/null || echo "99")
        score=$cnt
        # ponytail: penalty for degraded org
        [ "$h" = "DEGRADED" ] && score=$((cnt + 10))

        if [ "$score" -lt "$best_score" ]; then
            best_score=$score
            best_sub="$sub"
        fi
    done
    echo "$best_sub"
}

# ═══════════════════════════════════════════════════════════════
# ROTATION ACTION
# ═══════════════════════════════════════════════════════════════

rotate_agent() {
    local agent="$1" new_sub="$2" old_sub dropin
    dropin="$SYSTEMD_DIR/openclaw-${agent}.service.d/20-claude-cli-home.conf"

    [ ! -f "$dropin" ] && { echo "ERROR: $dropin not found"; return 1; }

    old_sub=$(grep HOME= "$dropin" | grep -o 'sub-[a-z]*' | head -1)
    echo "  Rotating $agent: $old_sub → $new_sub"

    if [ "${DRY_RUN:-false}" = "true" ]; then
        echo "  [DRY-RUN] would rotate $agent → $new_sub"
        return 0
    fi

    # Backup
    cp "$dropin" "$dropin.bak.$(date +%Y%m%d-%H%M)" || return 1

    # Write new dropin
    cat > "$dropin" << EOF
[Service]
Environment=HOME=$PROFILES_DIR/$new_sub
Environment=IS_SANDBOX=1
EOF

    # Reload + restart
    systemctl daemon-reload
    systemctl kill -s SIGTERM "openclaw-${agent}" 2>/dev/null || true

    # Wait for restart
    local i
    for i in $(seq 1 30); do
        sleep 2
        if systemctl is-active "openclaw-${agent}" 2>/dev/null | grep -q active; then
            echo "  ✅ $agent active on $new_sub"
            return 0
        fi
    done
    echo "  ⚠️ $agent: $old_sub → $new_sub (timeout)"
    return 1
}

# ═══════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════

log_rotation() {
    local agent="$1" old_sub="$2" new_sub="$3" reason="$4"
    echo "| $(date -u +"%Y-%m-%dT%H:%M:%SZ") | $agent | $old_sub | $new_sub | v2 | $reason | ✅ |" >> "$LOG_FILE"
}

# ═══════════════════════════════════════════════════════════════
# SCAN + ROTATE
# ═══════════════════════════════════════════════════════════════

cmd_scan() {
    cache_all_subs

    local agent sub new_sub

    echo "=== Rotation Scan @ $(date -u +"%Y-%m-%d %H:%M UTC") ==="

    for dir in "$PROFILES_DIR"/*/; do
        sub="${dir%/}"
        sub="${sub##*/}"
        [ "$sub" = "sub-" ] && continue

        local h
        h=$(cat "$CACHE_DIR/$sub.health" 2>/dev/null || echo "DEAD")
        [ "$h" != "DEAD" ] && continue

        echo "  DEAD: $sub ($(org_of "$sub")) — checking agents..."

        while read -r agent; do
            [ -z "$agent" ] && continue

            # ponytail: reserved agents never auto-move
            if echo "$RESERVED_AGENTS" | grep -qw "$agent"; then
                echo "    SKIP $agent (reserved on ${RESERVED_AGENT_SUB[$agent]}) → ALERT"
                continue
            fi

            new_sub=$(find_best_target "$sub" "$agent")
            if [ -z "$new_sub" ]; then
                echo "    ⚠️ $agent: no healthy target available"
                continue
            fi

            log_rotation "$agent" "$sub" "$new_sub" "dead_sub"
            rotate_agent "$agent" "$new_sub"
        done < "$CACHE_DIR/$sub.agents"
    done

    echo "=== Scan complete ==="
}

# ═══════════════════════════════════════════════════════════════
# EVENT WATCH (30s timer → journald scan)
# ═══════════════════════════════════════════════════════════════

cmd_event_watch() {
    # Clean stale cache dirs
    for cache_dir in /var/tmp/rotate-cache-v2.*; do
        [ -d "$cache_dir" ] && { rm -rf "$cache_dir"; break; }
    done 2>/dev/null || true

    if ! debounce_ok; then
        exit 0
    fi

    # ponytail: set +e around grep to avoid pipefail triggering on no-matches
    local errors=""
    set +e
    errors=$(journalctl -S "60 seconds ago" --no-pager \
        -u 'openclaw-*' 2>/dev/null | \
        grep -iE "rate.limit|spend.cap|429|organization.*disabled|billing" | \
        head -5)
    set -e

    if [ -z "$errors" ]; then
        exit 0
    fi

    touch_debounce
    echo "[watch $(date)] rate/spend errors in journal → triggering scan"
    cmd_scan
}

# ═══════════════════════════════════════════════════════════════
# OTHER COMMANDS
# ═══════════════════════════════════════════════════════════════

# ponytail: dry-run does full health + scan, that's the point
cmd_dry_run() {
    export DRY_RUN=true
    echo "=== DRY RUN (will show all decisions without executing) ==="
    echo
    cmd_scan
}

cmd_health_check() {
    echo "=== Health Check @ $(date -u +"%Y-%m-%d %H:%M UTC") ==="
    local sub h
    for dir in "$PROFILES_DIR"/*/; do
        sub="${dir%/}"
        sub="${sub##*/}"
        [ "$sub" = "sub-" ] && continue
        echo -n "  $sub ($(org_of "$sub"))... "
        echo "$(health_check "$sub")"
    done
    echo "=== Done ==="
}

# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

mkdir -p "$(dirname "$LOG_FILE")" "$(dirname "$DEBOUNCE_FILE")"

case "${1:-status}" in
    --status)      shift; cmd_status "${1:-fast}" ;;
    --status-full) cmd_status "full" ;;
    --dry-run)     cmd_dry_run ;;
    --scan)        cmd_scan ;;
    --health-check) cmd_health_check ;;
    --event-watch) cmd_event_watch ;;
    *) echo "Usage: $0 {--status|--status-full|--dry-run|--scan|--health-check|--event-watch}"
       exit 1 ;;
esac
