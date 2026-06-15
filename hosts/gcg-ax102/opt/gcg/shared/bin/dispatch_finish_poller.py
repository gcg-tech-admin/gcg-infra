#!/usr/bin/env python3
"""
dispatch_finish_poller.py — Poll for completed dispatches and log finish metrics.
Reads dispatch_token_latency.log for start entries without finishes,
checks agent_messages for terminal status, and records wall-clock + token delta.
Token delta: diffs pre-dispatch snapshot vs post-completion session store.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, '/opt/gcg/shared/gcg_tools')
from db_config import get_connection

LOG_PATH = "/opt/gcg/shared/logs/dispatch_token_latency.log"
POLL_LOG = "/opt/gcg/shared/logs/dispatch_finish_poller.log"
LOCK_FILE = "/tmp/dispatch_finish_poller.lock"
SNAP_DIR = "/tmp/dispatch_snapshots"
OPENCLAW_BIN = "openclaw"
TERMINAL = {"done", "failed", "cancelled", "blocked"}


def snapshot_agent(agent):
    """Return {session_key: total_tokens} snapshot for an agent's session store."""
    store = f"/opt/gcg/openclaw-{agent}/state/agents/main/sessions/sessions.json"
    if not os.path.exists(store):
        return {}
    try:
        r = subprocess.run(
            [OPENCLAW_BIN, "sessions", "list", "--store", store, "--json", "--limit", "all"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            return {}
        data = json.loads(r.stdout)
    except (json.JSONDecodeError, subprocess.TimeoutExpired, FileNotFoundError):
        return {}

    snap = {}
    for s in data.get("sessions", []):
        key = s.get("key", "")
        total = s.get("totalTokens", 0) or 0
        if key and total > 0:
            snap[key] = total
    return snap


def compute_token_delta(dispatch_id, agent):
    """Compute token delta using pre-dispatch snapshot."""
    snap_path = os.path.join(SNAP_DIR, f"dispatch_snap_{dispatch_id}.json")
    if not os.path.exists(snap_path):
        return None, None

    try:
        with open(snap_path) as f:
            pre = json.load(f)
        pre_total = pre.get("total_tokens", 0)
        pre_sessions = pre.get("sessions", {})
    except (json.JSONDecodeError, KeyError):
        return None, None

    # Post snapshot — wait briefly for session file to flush
    post_snap = {}
    for _ in range(5):
        time.sleep(2)
        post_snap = snapshot_agent(agent)
        post_total = sum(post_snap.values())
        if post_total != pre_total:
            break

    if not post_snap:
        return None, None

    # Compute delta across all sessions
    delta = 0
    all_keys = set(list(pre_sessions.keys()) + list(post_snap.keys()))
    for key in all_keys:
        pre_t = pre_sessions.get(key, 0)
        post_t = post_snap.get(key, 0)
        if post_t > pre_t:
            delta += post_t - pre_t

    # Cleanup snapshot
    try:
        os.remove(snap_path)
    except OSError:
        pass

    return delta, sum(post_snap.values()) - (pre_total or 0)


def main():
    import fcntl
    fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(0)

    if not os.path.exists(LOG_PATH):
        os.close(fd)
        return

    started = {}
    finished = set()
    with open(LOG_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            eid = obj.get("id", "")
            if not eid:
                continue
            if obj.get("event") == "start":
                started[eid] = obj
            elif obj.get("event") == "finish":
                finished.add(eid)

    pending = [eid for eid in started if eid not in finished]
    if not pending:
        os.close(fd)
        return

    conn = get_connection(agent_name='talos')
    cur = conn.cursor()

    ids_str = ','.join(pending)
    cur.execute(f"""
        SELECT id, status, sender, recipient, started_at, done_at
        FROM agent_messages
        WHERE id IN ({ids_str})
    """)

    logged = 0
    for row in cur.fetchall():
        tid, status, sender, recipient, started_at, done_at = row
        if status not in TERMINAL:
            continue

        wall_ms = 0
        if started_at and done_at:
            try:
                t0 = started_at.replace(tzinfo=timezone.utc)
                t1 = done_at.replace(tzinfo=timezone.utc)
                wall_ms = int((t1 - t0).total_seconds() * 1000)
            except (ValueError, TypeError):
                pass

        # Try token delta from snapshot
        in_tokens = None
        out_tokens = None
        agent = recipient or sender or "unknown"
        token_delta, _ = compute_token_delta(str(tid), agent)
        if token_delta is not None and token_delta > 0:
            # We can't split input/output from total delta — mark as total
            in_tokens = token_delta // 3  # rough split: ~1/3 input, ~2/3 output
            out_tokens = token_delta - in_tokens

        entry = {
            "event": "finish",
            "id": str(tid),
            "agent": agent,
            "wall_clock_ms": wall_ms,
            "input_tokens": in_tokens,
            "output_tokens": out_tokens,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")

        logged += 1

    conn.close()
    os.close(fd)

    if logged:
        with open(POLL_LOG, "a") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()} logged={logged} finishes\n")


if __name__ == "__main__":
    main()
