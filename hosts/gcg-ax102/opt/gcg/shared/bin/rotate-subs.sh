#!/bin/bash
# subscription-rotator.sh — GCG Fleet OAuth Subscription Manager
# Usage: ssh ax102 'bash -s' < scripts/rotate-subs.sh [analyze|agent N S|balance N|verify|list]
# Atlas, 2026-06-09

set -e

PROFILES_DIR="/opt/gcg/claude-profiles"
SYSTEMD_DIR="/etc/systemd/system"
MAX_DEFAULT=4  # agents before a sub is "overloaded"
THRESHOLD=${MAX_DEFAULT}

# ── Colors ──
RED=''; GREEN=''; YELLOW=''; NC=''

# ── Helpers ──
agent_count() {
    grep -rl "claude-profiles/$1" "$SYSTEMD_DIR"/openclaw-*.service.d/20-claude-cli-home.conf 2>/dev/null | wc -l | tr -d ' '
}

agents_on_sub() {
    grep -rl "claude-profiles/$1" "$SYSTEMD_DIR"/openclaw-*.service.d/20-claude-cli-home.conf 2>/dev/null | \
        sed 's|.*/openclaw-||;s|\.service\.d/20-claude-cli-home\.conf||' | sort | tr '\n' ' '
}

verify_sub_auth() {
    local sub="$1"
    HOME="$PROFILES_DIR/$sub" IS_SANDBOX=1 \
        timeout 30 claude --model claude-sonnet-4-6 -p "Reply with exactly AUTH_OK" 2>&1 | \
        grep -q "AUTH_OK" && return 0 || return 1
}

rotate_agent() {
    local agent="$1" new_sub="$2"
    local dropin="$SYSTEMD_DIR/openclaw-${agent}.service.d/20-claude-cli-home.conf"

    if [[ ! -f "$dropin" ]]; then
        echo "${RED}ERROR: $dropin not found${NC}"
        return 1
    fi

    local old_sub=$(grep HOME= "$dropin" | grep -o 'sub-[a-z]*' | head -1)

    echo "Rotating $agent: $old_sub → $new_sub"

    # 1. Backup
    cp "$dropin" "$dropin.bak.rotate-$(date +%Y%m%d-%H%M)" || return 1

    # 2. Verify auth
    echo -n "  Verifying $new_sub auth... "
    if verify_sub_auth "$new_sub"; then
        echo "${GREEN}OK${NC}"
    else
        echo "${RED}FAILED${NC}"
        return 1
    fi

    # 3. Write
    cat > "$dropin" << EOF
[Service]
Environment=HOME=$PROFILES_DIR/$new_sub
Environment=IS_SANDBOX=1
EOF

    # 4. Restart
    systemctl daemon-reload
    systemctl restart "openclaw-${agent}" 2>/dev/null || systemctl kill -s SIGTERM "openclaw-${agent}" 2>/dev/null || true
    echo -n "  Restarting... "
    for i in $(seq 1 30); do
        sleep 2
        if systemctl is-active "openclaw-${agent}" 2>/dev/null | grep -q active; then
            echo "${GREEN}active${NC}"
            return 0
        fi
    done
    echo "${RED}TIMEOUT${NC}"
    return 1
}

# ── Commands ──

cmd_list() {
    echo "=== GCG OAuth Subscription Map ==="
    printf "%-15s %7s  %s\n" "SUB" "AGENTS" "STATUS"
    printf "%-15s %7s  %s\n" "---" "------" "------"
    for dir in "$PROFILES_DIR"/*/; do
        sub=$(basename "$dir")
        count=$(agent_count "$sub")
        status="OK"
        [[ $count -eq 0 ]] && status="IDLE"
        [[ $count -gt $THRESHOLD ]] && status="${RED}OVERLOADED${NC}"
        printf "%-15s %7s  %b\n" "$sub" "$count" "$status"
    done
    echo
}

cmd_analyze() {
    cmd_list
    echo "=== Detailed Agent Assignments ==="
    for dir in "$PROFILES_DIR"/*/; do
        sub=$(basename "$dir")
        count=$(agent_count "$sub")
        agents=$(agents_on_sub "$sub")
        echo "  $sub ($count): $agents"
    done
    echo
    echo "=== Overloaded (>=$THRESHOLD) ==="
    local overloaded=0
    for dir in "$PROFILES_DIR"/*/; do
        sub=$(basename "$dir")
        count=$(agent_count "$sub")
        if [[ $count -gt $THRESHOLD ]]; then
            echo "  ${RED}$sub: $count agents${NC}"
            overloaded=1
        fi
    done
    [[ $overloaded -eq 0 ]] && echo "  ${GREEN}None${NC}"
    echo
    echo "=== Idle (0 agents) ==="
    for dir in "$PROFILES_DIR"/*/; do
        sub=$(basename "$dir")
        count=$(agent_count "$sub")
        [[ $count -eq 0 ]] && echo "  $sub"
    done
}

cmd_verify() {
    echo "=== Verifying All Subs ==="
    for dir in "$PROFILES_DIR"/*/; do
        sub=$(basename "$dir")
        echo -n "  $sub... "
        if verify_sub_auth "$sub"; then
            echo "${GREEN}AUTH_OK${NC}"
        else
            echo "${RED}AUTH_FAIL${NC}"
        fi
    done
}

cmd_agent() {
    local agent="$1" sub="$2"
    if [[ -z "$agent" || -z "$sub" ]]; then
        echo "Usage: agent <agent_name> <sub_name>"
        echo "Example: agent daen sub-sergei"
        exit 1
    fi
    rotate_agent "$agent" "$sub"
}

cmd_balance() {
    local max="${1:-$MAX_DEFAULT}"
    THRESHOLD=$max
    echo "=== Auto-Balancing (max $max agents/sub) ==="

    # Find overloaded subs
    for dir in "$PROFILES_DIR"/*/; do
        sub=$(basename "$dir")
        count=$(agent_count "$sub")
        if [[ $count -le $max ]]; then continue; fi

        excess=$((count - max))
        agents=$(agents_on_sub "$sub")

        echo "  $sub has $count agents (excess: $excess)"
        echo "  Agents: $agents"

        # Find idle subs
        for idir in "$PROFILES_DIR"/*/; do
            isub=$(basename "$idir")
            icount=$(agent_count "$isub")
            if [[ $icount -lt $max ]]; then
                # Move first excess agent
                for a in $agents; do
                    if [[ $icount -lt $max ]]; then
                        echo "  → Moving $a to $isub"
                        rotate_agent "$a" "$isub" || echo "  ${RED}FAILED${NC}"
                        icount=$((icount + 1))
                        count=$((count - 1))
                        [[ $count -le $max ]] && break
                    fi
                done
            fi
            [[ $count -le $max ]] && break
        done
    done
    echo "=== Balance Complete ==="
    cmd_list
}

# ── Main ──
case "${1:-analyze}" in
    analyze)  cmd_analyze ;;
    list)     cmd_list ;;
    verify)   cmd_verify ;;
    agent)    cmd_agent "$2" "$3" ;;
    balance)  cmd_balance "${2:-$MAX_DEFAULT}" ;;
    *)        echo "Usage: rotate-subs.sh {analyze|list|verify|agent <a> <s>|balance [max]}"; exit 1 ;;
esac
