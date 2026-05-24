#!/usr/bin/env python3
"""
openclaw-config-audit.py v3 — Layer 4 protection: inotify-based audit daemon
Watches openclaw.json for all agents. Detects SELF_WRITE vs LEGITIMATE_WRITE.

SELF_WRITE detection (two paths):
  1. lsof catches the gateway actively holding the file open (IN_MODIFY).
  2. CWD_RESIDENT correlation: if the ONLY /proc cwd residents for an agent are
     that agent's own gateway process(es) (node/openclaw), and no other writer was
     detected by lsof, we classify as SELF_WRITE (the agent is the only process
     that could have written). A known non-agent writer (bootstrap, operator shell,
     python script) found by lsof overrides this to LEGITIMATE_WRITE.

DB alerts: critical-6 SELF_WRITE only, on IN_MODIFY event only (dedup).
"""

import os
import sys
import logging
import datetime
import subprocess
import psycopg2
import pyinotify

OPENCLAW_BASE = "/opt/gcg"
LOG_FILE = "/var/log/gcg-config-audit.log"
CRITICAL_6 = {"daen", "talos", "marcus", "mnemosyne", "vulcan", "nik"}

# Non-agent writer signatures — indicate operator/system writes
OPERATOR_SIGS = (
    "gcg-secret-bootstrap",
    "/bin/bash", "/usr/bin/bash", "/bin/sh", "/usr/bin/sh",
    "sshd", "ssh-session",
    "fleet", "chattr", "vim", "nano",
    "openclaw-config-audit",
)

# Load DB creds from mnemosyne env
DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD = "95.217.114.49", 5432, "gcg_intelligence", "gcg_mnemosyne", ""
try:
    with open("/run/openclaw-mnemosyne/env") as _f:
        for _line in _f:
            k, _, v = _line.strip().partition("=")
            if k == "GCG_DB_PASSWORD": DB_PASSWORD = v
            elif k == "GCG_DB_HOST": DB_HOST = v
            elif k == "GCG_DB_USER": DB_USER = v
            elif k == "GCG_DB_NAME": DB_NAME = v
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO, format="%(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("config-audit")


def get_agent_name(path):
    try:
        return path.split("/")[-2].replace("openclaw-", "", 1)
    except Exception:
        return None


def proc_info(pid_str):
    """Return (cmdline, cwd, exe) for a /proc PID."""
    try:
        cmdline = open(f"/proc/{pid_str}/cmdline").read().replace("\0", " ").strip()
    except Exception:
        cmdline = "unknown"
    try:
        cwd = os.readlink(f"/proc/{pid_str}/cwd")
    except Exception:
        cwd = "unknown"
    try:
        exe = os.readlink(f"/proc/{pid_str}/exe")
    except Exception:
        exe = ""
    return cmdline, cwd, exe


def lsof_writers(path):
    """Return list of (pid, cmdline, cwd, exe) for processes with path open."""
    writers = []
    try:
        r = subprocess.run(["lsof", "-F", "pn", path], capture_output=True, text=True, timeout=4)
        pids = set()
        for line in r.stdout.splitlines():
            if line.startswith("p"):
                pids.add(line[1:].strip())
        for p in pids:
            writers.append((p, *proc_info(p)))
    except Exception:
        pass
    return writers


def cwd_residents(agent_dir):
    """Scan /proc for processes with cwd == agent_dir."""
    residents = []
    try:
        for pid_str in os.listdir("/proc"):
            if not pid_str.isdigit():
                continue
            try:
                if os.readlink(f"/proc/{pid_str}/cwd") == agent_dir:
                    residents.append((pid_str, *proc_info(pid_str)))
            except Exception:
                continue
    except Exception:
        pass
    return residents


def is_gateway(cmdline, exe):
    """True if process looks like an openclaw gateway (node)."""
    return exe in ("/usr/bin/node", "/usr/local/bin/node") or "openclaw" in cmdline.lower()


def is_operator(cmdline, exe):
    """True if process is a known legitimate operator/system writer."""
    return any(sig in cmdline for sig in OPERATOR_SIGS)


def classify(agent_name, lsof_ws, cwd_res):
    """Returns (classification, detail_str)."""
    agent_dir = f"/opt/gcg/openclaw-{agent_name}"
    parts = []
    for pid, cmdline, cwd, exe in lsof_ws:
        parts.append(f"lsof:pid={pid} cmd=[{cmdline[:60]}] cwd={cwd}")
    for pid, cmdline, cwd, exe in cwd_res:
        parts.append(f"cwd_res:pid={pid} cmd=[{cmdline[:60]}]")
    detail = "; ".join(parts) if parts else "no_writers_detected"

    # lsof found agent's own gateway actively holding file
    for pid, cmdline, cwd, exe in lsof_ws:
        if cwd == agent_dir and is_gateway(cmdline, exe):
            return "SELF_WRITE", detail

    # lsof found known operator/bootstrap writer
    for pid, cmdline, cwd, exe in lsof_ws:
        if is_operator(cmdline, exe):
            return "LEGITIMATE_WRITE", detail

    # lsof found unrecognized writers
    if lsof_ws:
        return "UNKNOWN_WRITE", detail

    # No lsof writers (file closed before scan) — use cwd_res correlation
    non_audit_res = [
        (p, c, cw, e) for p, c, cw, e in cwd_res
        if "openclaw-config-audit" not in c
    ]

    if non_audit_res:
        # If only the agent's own gateway(s) live here: they wrote it
        if all(is_gateway(c, e) for _, c, _, e in non_audit_res):
            return "SELF_WRITE", detail + " [correlated:only_gateway_in_cwd]"
        # Mix: operator python or other script present
        if any(is_operator(c, e) for _, c, _, e in non_audit_res):
            return "LEGITIMATE_WRITE", detail + " [correlated:operator_resident]"

    return "UNKNOWN_WRITE", detail


def write_db_alert(agent_name, classification, detail):
    if not DB_PASSWORD:
        logger.warning(f"[DB_ALERT_SKIP] No DB creds for {agent_name}")
        return
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
            sslmode="require", connect_timeout=5,
        )
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO mnemosyne_audit (check_type, status, detail) VALUES (%s, %s, %s)",
                    (f"config_write_{classification.lower()}",
                     "ALERT" if classification == "SELF_WRITE" else "WARN",
                     detail[:2000]),
                )
        conn.close()
        logger.info(f"[DB_ALERT_OK] {classification} alert for {agent_name} written to mnemosyne_audit")
    except Exception as e:
        logger.error(f"[DB_ALERT_FAIL] {e}")


def handle_event(path, event_name):
    if not path.endswith("openclaw.json"):
        return
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    agent_name = get_agent_name(path)
    if not agent_name:
        return

    agent_dir = f"/opt/gcg/openclaw-{agent_name}"
    lsof_ws = lsof_writers(path)
    cwd_res = cwd_residents(agent_dir)
    classification, detail = classify(agent_name, lsof_ws, cwd_res)
    is_c6 = agent_name in CRITICAL_6

    if is_c6 and classification == "SELF_WRITE":
        tag = "[CRITICAL6-SELF-WRITE]"
    else:
        tag = f"[{classification}]"

    log_line = (
        f"{tag} ts={ts} event={event_name} agent={agent_name} "
        f"critical6={is_c6} writers=[{detail}] path={path}"
    )
    logger.info(log_line)

    # DB alert: critical-6 SELF_WRITE, IN_MODIFY only (dedup — not double-fire on CLOSE_WRITE)
    if is_c6 and classification == "SELF_WRITE" and event_name == "IN_MODIFY":
        write_db_alert(agent_name, classification, log_line)


class Handler(pyinotify.ProcessEvent):
    def process_IN_MODIFY(self, event):
        handle_event(event.pathname, "IN_MODIFY")
    def process_IN_CLOSE_WRITE(self, event):
        handle_event(event.pathname, "IN_CLOSE_WRITE")


def main():
    logger.info(f"[STARTUP] openclaw-config-audit v3 pid={os.getpid()}")
    wm = pyinotify.WatchManager()
    notifier = pyinotify.Notifier(wm, Handler())
    mask = pyinotify.IN_MODIFY | pyinotify.IN_CLOSE_WRITE
    dirs = []
    for entry in sorted(os.listdir(OPENCLAW_BASE)):
        if not entry.startswith("openclaw-"):
            continue
        d = os.path.join(OPENCLAW_BASE, entry)
        if not os.path.isfile(os.path.join(d, "openclaw.json")):
            continue
        dirs.append(d)
        agent = entry.replace("openclaw-", "", 1)
        wm.add_watch(d, mask, rec=False)
        logger.info(f"[WATCH] {d} (agent={agent} critical6={agent in CRITICAL_6})")
    logger.info(f"[STARTUP] Watching {len(dirs)} agent dirs. Entering event loop.")
    notifier.loop()


if __name__ == "__main__":
    main()
