#!/bin/bash
# claude-tmux-healthcheck.sh — GCG Claude bridge v31 (Atlas, 2026-06-11)
# Runs every 5 min. Ensures the 5 dedicated interactive Claude sessions are alive.
# Conservative: a session is recreated ONLY if it is gone, or its REPL footer /
# working indicator has been absent for two checks 5s apart (i.e. it dropped to a
# bash shell or crashed). This avoids killing a session mid-turn based on its output.

SESSIONS=(
  "daen sub-sergei claude-opus-4-8"
  "marcus sub-sonja claude-sonnet-4-6"
  "kenji sub-flore claude-sonnet-4-6"
  "nik sub-sonja claude-sonnet-4-6"
  "vera sub-flore claude-sonnet-4-6"
)

is_healthy() {
  local sess="$1"
  tmux has-session -t "$sess" 2>/dev/null || return 1
  local pane
  pane=$(tmux capture-pane -t "$sess" -p -S -5 2>/dev/null)
  echo "$pane" | grep -qiE "for shortcuts|to interrupt|esc to interrupt" && return 0
  return 1
}

create() {
  local agent=$1 profile=$2 cmodel=$3
  local sess="gcg-claude-${cmodel}-${agent}"
  local cwd="/opt/gcg/shared/bridge-sessions/${agent}"
  local home="/opt/gcg/claude-profiles/${profile}"
  mkdir -p "$cwd"
  tmux kill-session -t "$sess" 2>/dev/null || true
  tmux new-session -d -s "$sess" -c "$cwd" -x 200 -y 50
  tmux set-option -t "$sess" history-limit 5000 2>/dev/null || true
  tmux send-keys -t "$sess" "unset ANTHROPIC_API_KEY" C-m
  sleep 0.5
  tmux send-keys -t "$sess" "HOME=$home IS_SANDBOX=1 DISABLE_AUTOUPDATER=1 claude --model $cmodel --permission-mode bypassPermissions" C-m
  logger -t gcg-claude-health "recreated $sess (profile=$profile)"
}

for spec in "${SESSIONS[@]}"; do
  set -- $spec
  agent=$1; profile=$2; cmodel=$3
  sess="gcg-claude-${cmodel}-${agent}"
  if ! is_healthy "$sess"; then
    sleep 5
    if ! is_healthy "$sess"; then
      logger -t gcg-claude-health "UNHEALTHY $sess — recreating"
      create "$agent" "$profile" "$cmodel"
    fi
  fi
done
