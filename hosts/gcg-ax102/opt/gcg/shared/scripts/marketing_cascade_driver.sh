#!/bin/bash
# Marketing V4 Cascade Driver — daily at 9am Dubai
# Checks progress, dispatches unblocked work, alerts Peter ONLY if he's the bottleneck
set -euo pipefail

LOG="/var/log/gcg-marketing-driver.log"
DISPATCH="/opt/gcg/shared/scripts/claude_code_dispatch.sh"
FLEET="/opt/gcg/shared/bin/fleet"
PAT=$(cat /opt/gcg/shared/credentials/asana_pat_daen.txt)
PROJECT="1214028028508718"
PETER_CHAT="418059105"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }
log "=== MARKETING DRIVER RUN ==="

# Get all tasks
TASKS=$(curl -s -H "Authorization: Bearer $PAT" \
  "https://app.asana.com/api/1.0/projects/$PROJECT/tasks?opt_fields=name,completed,assignee.name&limit=100")

TOTAL=$(echo "$TASKS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('data',[])))")
DONE=$(echo "$TASKS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len([t for t in d.get('data',[]) if t['completed']]))")
PENDING=$(echo "$TASKS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len([t for t in d.get('data',[]) if not t['completed']]))")

log "Progress: $DONE/$TOTAL done, $PENDING pending"

# Find Peter-blocked tasks (assigned to daen or unassigned human gates)
PETER_BLOCKED=$(echo "$TASKS" | python3 -c "
import sys,json
d = json.load(sys.stdin)
blocked = []
for t in d.get('data',[]):
    if t['completed']: continue
    name = t['name']
    assignee = t.get('assignee',{})
    aname = assignee.get('name','') if assignee else ''
    # Peter gates: anything with 'Peter', 'BLOCKER', 'approval', or assigned to Daen (tracking for Peter)
    if 'Peter' in name or 'BLOCKER' in name or 'approval' in name.lower():
        blocked.append(name[:60])
for b in blocked:
    print(b)
")

# Find dispatchable tasks (not done, not QA gates, not Peter-blocked)
DISPATCHABLE=$(echo "$TASKS" | python3 -c "
import sys,json
d = json.load(sys.stdin)
for t in d.get('data',[]):
    if t['completed']: continue
    name = t['name']
    if 'Vulcan QA' in name or 'Peter' in name or 'BLOCKER' in name or 'approval' in name.lower() or 'Autonomy gate' in name:
        continue
    print(t['gid'] + '|' + name[:80])
")

DISPATCH_COUNT=$(echo "$DISPATCHABLE" | grep -c '|' || echo 0)
log "Dispatchable tasks: $DISPATCH_COUNT"

# Dispatch up to 5 unblocked tasks to Claude Code
DISPATCHED=0
while IFS='|' read -r GID NAME; do
    [ -z "$GID" ] && continue
    [ $DISPATCHED -ge 5 ] && break
    
    if [ ! -f /opt/gcg/shared/dispatch/task.md ]; then
        $DISPATCH "MARKETING V4 — $NAME. SSH to Hetzner (root@10.0.0.2). Read the full plan context from CONTEXT.md. Execute this task. Report what you did with file paths." --requester anna &
        wait
        DISPATCHED=$((DISPATCHED + 1))
        log "Dispatched: $NAME"
    fi
done <<< "$DISPATCHABLE"

# Alert Peter ONLY if he's the bottleneck
if [ -n "$PETER_BLOCKED" ]; then
    BLOCK_COUNT=$(echo "$PETER_BLOCKED" | grep -c '.' || echo 0)
    log "Peter-blocked: $BLOCK_COUNT tasks"
    
    # Send daily summary to Daen (who relays to Peter only if critical)
    $FLEET send daen "MARKETING DAILY: $DONE/$TOTAL done. $BLOCK_COUNT tasks need Peter. Dispatched $DISPATCHED new tasks to Claude Code today.

Peter-blocked:
$PETER_BLOCKED" 2>/dev/null || true
fi

log "Dispatched $DISPATCHED tasks. === DRIVER COMPLETE ==="
