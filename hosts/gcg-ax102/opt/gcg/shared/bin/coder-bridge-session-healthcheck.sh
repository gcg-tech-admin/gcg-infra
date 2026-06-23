#!/bin/bash
# coder-bridge-session-healthcheck.sh — Daen, 2026-06-22
# Keeps DEDICATED coder-bridge tmux sessions alive. The main
# claude-tmux-healthcheck.sh only covers the 5 agent sessions and NOT the
# executor bridge sessions — so when a bridge's tmux session died, the
# gcg-coder-bridge daemon went headless ("session not found — skipping tick")
# and every dispatched task piled up pending forever. This closes that gap.
#
# Scope guard: only heals sessions whose name starts with "gcg-bridge-".
# Bridges that point at a shared agent session (e.g. coder-opus ->
# gcg-claude-...-daen) are owned by the main healthcheck; we skip them to
# avoid two managers fighting over one session.
#
# Conservative: recreate ONLY if the session is gone, or its REPL footer has
# been absent across two checks 5s apart (dropped to a bare shell / crashed).

CONF_DIR="/etc/gcg/coder-bridge"

is_healthy() {
  local sess="$1"
  tmux has-session -t "$sess" 2>/dev/null || return 1
  local pane
  pane=$(tmux capture-pane -t "$sess" -p -S -5 2>/dev/null)
  echo "$pane" | grep -qiE "for shortcuts|to interrupt|esc to interrupt" && return 0
  return 1
}

create() {
  local sess="$1" home="$2" cmodel="$3" agent="$4"
  local cwd="/opt/gcg/shared/bridge-sessions/${agent}"
  mkdir -p "$cwd"
  tmux kill-session -t "$sess" 2>/dev/null || true
  tmux new-session -d -s "$sess" -c "$cwd" -x 200 -y 50
  tmux set-option -t "$sess" history-limit 5000 2>/dev/null || true
  # OAuth ONLY — never ANTHROPIC_API_KEY (Peter standing rule). Sandbox env
  # is required so v2.1.150 accepts --permission-mode bypassPermissions for root.
  tmux send-keys -t "$sess" "unset ANTHROPIC_API_KEY" C-m
  sleep 0.5
  tmux send-keys -t "$sess" "HOME=$home IS_SANDBOX=1 DISABLE_AUTOUPDATER=1 claude --model $cmodel --permission-mode bypassPermissions" C-m
  logger -t gcg-coder-bridge-health "recreated $sess (home=$home model=$cmodel)"
}

for conf in "$CONF_DIR"/*.conf; do
  [ -f "$conf" ] || continue
  AGENT_NAME=""; TMUX_SESSION=""; PROFILE_HOME=""
  # shellcheck source=/dev/null
  source "$conf"
  [ -n "$TMUX_SESSION" ] && [ -n "$PROFILE_HOME" ] && [ -n "$AGENT_NAME" ] || continue
  # Scope guard: only dedicated bridge sessions.
  case "$TMUX_SESSION" in
    gcg-bridge-*) ;;
    *) continue ;;
  esac
  # Derive model from session name: gcg-bridge-<model>
  CMODEL="${TMUX_SESSION#gcg-bridge-}"
  [ -n "$CMODEL" ] || continue
  if ! is_healthy "$TMUX_SESSION"; then
    sleep 5
    if ! is_healthy "$TMUX_SESSION"; then
      logger -t gcg-coder-bridge-health "UNHEALTHY $TMUX_SESSION — recreating"
      create "$TMUX_SESSION" "$PROFILE_HOME" "$CMODEL" "$AGENT_NAME"
    fi
  fi
done
