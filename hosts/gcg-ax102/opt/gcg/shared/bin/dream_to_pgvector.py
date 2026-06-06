#!/usr/bin/env python3
"""dream_to_pgvector — promote OpenClaw Dreaming/short-term consolidations into GCG pgvector.

OpenClaw Dreaming consolidates short-term recall into MEMORY.md (native file/sqlite
memory). This bridge takes the SAME gate-passing candidates that the deep phase
promotes and captures them into the GCG pgvector store via `memory capture`, so
unified `memory recall` surfaces them.

Idempotent: dedups on the candidate `key` via a per-agent checkpoint at
<workspace>/memory/.dreams/pgvector-promoted.json. Re-running never double-embeds.

Usage:
  dream_to_pgvector.py --agent talos [--min-score 0.80] [--min-recall-count 5]
                       [--min-unique-queries 4] [--limit 100] [--dry-run]
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Daily fleet-task / cron / heartbeat logs are operational noise, not durable
# knowledge. They get indexed into short-term recall and re-recalled by the
# heartbeat's own recall queries, so they rank high but are worthless to promote.
# They share a `## [HH:MM] <kind>: ... / Status: ... / Key details: ...` shape.
_DAILY_LOG = re.compile(r"memory/\d{4}-\d{2}-\d{2}\.md$")
_TS_HEADER = re.compile(r"##\s*\[\d{1,2}:\d{2}\]")


def is_operational_log(c: dict) -> bool:
    path = c.get("path", "")
    snippet = c.get("snippet", "")
    headers = _TS_HEADER.findall(snippet)
    if not headers:
        return False
    # Two or more timestamped entries = a log block.
    if len(headers) >= 2:
        return True
    # Single entry that carries the status/cron log markers, or sits in a daily log.
    if "Status:" in snippet or "Key details:" in snippet:
        return True
    return bool(_DAILY_LOG.search(path))

WORKSPACES = {
    # agent -> workspace dir (extend as fleet grows)
    "talos": "/opt/gcg/openclaw-talos/workspace",
    "daen": "/opt/gcg/openclaw-daen/workspace",
}
MEMORY_BIN = "/opt/gcg/shared/bin/memory"
SOURCE_TYPE = "dreaming_promoted"


def workspace_for(agent: str) -> Path:
    if agent in WORKSPACES:
        return Path(WORKSPACES[agent])
    # fallback: conventional layout
    return Path(f"/opt/gcg/openclaw-{agent}/workspace")


def load_checkpoint(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return {"promoted": {}}
    return {"promoted": {}}


def save_checkpoint(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def fetch_candidates(ws: Path, min_score: float, min_recall: int,
                     min_unique: int, limit: int) -> list:
    # No --agent: the CLI's default agent IS this workspace's agent. Passing
    # --agent re-roots into a wrong nested subdir. cwd pins the right store.
    cmd = [
        "openclaw", "memory", "promote", "--json", "--include-promoted",
        "--min-score", str(min_score),
        "--min-recall-count", str(min_recall),
        "--min-unique-queries", str(min_unique),
        "--limit", str(limit),
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ws))
    if out.returncode != 0:
        sys.stderr.write(f"[dream_to_pgvector] promote failed: {out.stderr}\n")
        sys.exit(1)
    data = json.loads(out.stdout)
    return data.get("candidates", [])


def fingerprint(c: dict) -> str:
    # key is stable per recall entry; hash snippet too so edited content re-promotes.
    raw = f"{c.get('key','')}|{c.get('snippet','')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def capture(agent: str, snippet: str, importance: float) -> bool:
    cmd = [
        MEMORY_BIN, "capture", snippet,
        "--agent", agent,
        "--source-type", SOURCE_TYPE,
        "--memory-type", "fact",
        "--scope", "agent",
        "--importance", str(importance),
    ]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        sys.stderr.write(f"[dream_to_pgvector] capture failed: {out.stderr.strip()[:200]}\n")
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True)
    ap.add_argument("--min-score", type=float, default=0.80)
    ap.add_argument("--min-recall-count", type=int, default=5)
    ap.add_argument("--min-unique-queries", type=int, default=4)
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    ws = workspace_for(args.agent)
    if not ws.exists():
        sys.stderr.write(f"[dream_to_pgvector] no workspace for agent '{args.agent}': {ws}\n")
        return 1

    ckpt_path = ws / "memory" / ".dreams" / "pgvector-promoted.json"
    ckpt = load_checkpoint(ckpt_path)
    promoted = ckpt.setdefault("promoted", {})

    candidates = fetch_candidates(
        ws, args.min_score, args.min_recall_count,
        args.min_unique_queries, args.limit,
    )

    new, skipped, filtered, failed = 0, 0, 0, 0
    now = datetime.now(timezone.utc).isoformat()
    for c in candidates:
        if is_operational_log(c):
            filtered += 1
            continue
        fp = fingerprint(c)
        if fp in promoted:
            skipped += 1
            continue
        snippet = (c.get("snippet") or "").strip()
        if not snippet:
            continue
        importance = round(min(1.0, float(c.get("maxScore", 0.8))), 3)
        if args.dry_run:
            print(f"[DRY] would capture {c.get('key')} (score={importance})")
            new += 1
            continue
        if capture(args.agent, snippet, importance):
            promoted[fp] = {
                "key": c.get("key"),
                "path": c.get("path"),
                "maxScore": c.get("maxScore"),
                "recallCount": c.get("recallCount"),
                "promotedAt": now,
            }
            new += 1
        else:
            failed += 1

    if not args.dry_run:
        ckpt["lastRun"] = now
        save_checkpoint(ckpt_path, ckpt)

    print(f"[dream_to_pgvector] agent={args.agent} candidates={len(candidates)} "
          f"new={new} skipped={skipped} filtered={filtered} failed={failed} "
          f"{'(dry-run)' if args.dry_run else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
