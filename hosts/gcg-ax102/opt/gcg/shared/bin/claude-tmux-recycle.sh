#!/bin/bash
# claude-tmux-recycle.sh — GCG Claude bridge v31 (Atlas, 2026-06-11)
# Daily recycle at 04:00 Asia/Dubai (00:00 UTC) to bound interactive-session context
# growth. Kills and recreates the 5 dedicated sessions, staggered to avoid the Claude
# auto-update binary-swap race (DISABLE_AUTOUPDATER also set as belt-and-suspenders).

logger -t gcg-claude-recycle "Starting daily session recycle (v31)"

SESSIONS=(
  "daen sub-sergei claude-opus-4-8"
  "marcus sub-ishan claude-sonnet-4-6"
  "kenji sub-flore claude-sonnet-4-6"
  "nik sub-sonja claude-sonnet-4-6"
  "vera sub-flore claude-sonnet-4-6"
)

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
  tmux send-keys -t "$sess" "HOME=$home IS_SANDBOX=1 DISABLE_AUTOUPDATER=1 claude --model $cmodel" C-m
  logger -t gcg-claude-recycle "recreated $sess (profile=$profile)"
}

for spec in "${SESSIONS[@]}"; do
  set -- $spec
  create "$1" "$2" "$3"
  sleep 12   # stagger to avoid claude auto-update binary-swap race across sessions
done

logger -t gcg-claude-recycle "Daily session recycle complete"
