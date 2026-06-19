#!/usr/bin/env python3
"""
council_convene.py — Convene a new council.

Validates plan path, writes manifest, dispatches round 1, exits.
The cron tick driver takes over from here.

Usage:
    council_convene.py --slug <slug> --plan <plan_path> [--goal "<goal>"]
                       [--key-finding "<finding>"] [--pattern "<pattern>"]
                       [--planner <agent>] [--deadline <hours>]
"""

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

COUNCILS_DIR = Path("/opt/gcg/shared/councils")
REVIEWERS = ["SOCRATES", "NEMESIS", "CASSANDRA", "CONFUCIUS", "WONHOO"]
REVIEWER_IDS = {
    "SOCRATES": "socrates",
    "NEMESIS": "nemesis",
    "CASSANDRA": "cassandra",
    "CONFUCIUS": "confucius",
    "WONHOO": "wonhoo",
    "socrates": "socrates",
    "nemesis": "nemesis",
    "cassandra": "cassandra",
    "confucius": "confucius",
    "wonhoo": "wonhoo",
}


def validate_plan(plan_path: str) -> str:
    """
    Validate plan path exists, is readable, and compute hash.
    Fail noisily on bad input (Nemesis M2).
    """
    path = Path(plan_path)

    if not path.exists():
        print(f"ERROR: Plan file not found: {plan_path}", file=sys.stderr)
        sys.exit(1)

    if not path.is_file():
        print(f"ERROR: Not a file: {plan_path}", file=sys.stderr)
        sys.exit(1)

    if not os.access(str(path), os.R_OK):
        print(f"ERROR: Plan file not readable: {plan_path}", file=sys.stderr)
        sys.exit(1)

    content = path.read_bytes()
    file_hash = "sha256:" + hashlib.sha256(content).hexdigest()
    print(f"  Plan hash: {file_hash[:40]}...")
    return file_hash


def build_manifest(slug: str, plan_path: str, plan_hash: str,
                   reviewer_list: list[str],
                   planner_agent: str = "daen",
                   deadline_hours: int = 2) -> dict:
    """Build initial council manifest."""
    return {
        "slug": slug,
        "plan_path": plan_path,
        "plan_hash": plan_hash,
        "round": 1,
        "reviewers": reviewer_list,
        "status": "in_review",
        "stuck_count": 0,
        "planner_deadline_h": deadline_hours,
        "planner_agent": planner_agent,
        "escalated_at": None,
        "last_tick": datetime.now(timezone.utc).isoformat(),
        "history": [],
    }


def dispatch_round1(slug: str, plan_path: str, plan_hash: str,
                    reviewer_list: list[str], goal: str = "",
                    key_finding: str = "", pattern: str = ""):
    """Dispatch round 1 to all reviewers."""
    roles = {
        "SOCRATES": "Question every assumption. Probe logic, expose contradictions, ask what the builder took for granted.",
        "NEMESIS": "Attack execution. Find what breaks under stress, what fails on agent 15 but not agent 1, what the rollback doesn't cover.",
        "CASSANDRA": "Project the future. What does this plan become in 3-6 months? Technical debt, scaling walls, lock-in, second-order effects.",
        "CONFUCIUS": "Verify every factual claim. Read the actual files. Check real configs. Confirm paths exist. Do NOT speculate — go look.",
        "WONHOO": "Practical feasibility. Will this actually work when a real person or agent executes it? Are steps clear? Timeline realistic? Simpler way?",
    }

    for r_name in reviewer_list:
        r_upper = r_name.upper()
        agent_id = REVIEWER_IDS.get(r_upper, r_name)
        role = roles.get(r_upper, "Review the plan.")

        vf_path = Path(f"/opt/gcg/shared/plans/reviews/{slug}-{r_upper}.md")

        msg = (
            f"COUNCIL REVIEW REQUEST — {slug} (Round 1)\n\n"
            f"Goal: {goal} | Plan: {plan_path}"
            + (f" | Key finding: {key_finding}" if key_finding else "")
            + (f" | Prior pattern: {pattern}" if pattern else "") + "\n\n"
            f"Your role: {role}\n\n"
            f"MANDATORY SEQUENCE:\n"
            f"1. READ workspace/SOUL.md — your identity and review principles.\n"
            f"2. READ workspace/memory/review-log.md — scan ALL past entries for patterns applicable to this plan.\n"
            f"3. REVIEW the plan against your role. Read the actual plan file at: {plan_path}\n"
            f"4. WRITE verdict to {vf_path} using EXACTLY this format:\n\n"
            f"## Verdict\n"
            f"**VERDICT: [PASS|FAIL|CONDITIONAL]**\n\n"
            f"### Prior patterns applied\n"
            f"- [Pattern from your review-log if applicable, else 'None applicable']\n\n"
            f"### Key findings\n"
            f"- [Finding 1]\n"
            f"- [Finding 2]\n\n"
            f"### Required changes (FAIL/CONDITIONAL only)\n"
            f"- [ROOT CAUSE + specific change required before PASS — not a symptom fix]\n\n"
            f"5. EMBED to pgvector: ...\n"
            f"6. APPEND hash footer:\n"
            f"   reviewed-hash: {plan_hash}\n"
            f"7. CLOSE inbox: fleet done <message_id> && rm workspace/inbox/<message_id>.json"
        )

        subprocess.run(["fleet", "send", agent_id, msg], check=True)
        print(f"  → {r_upper}: dispatched (round 1, agent={agent_id})")


def main():
    parser = argparse.ArgumentParser(description="Convene a new council")
    parser.add_argument("--slug", required=True, help="Council slug (e.g. provenance-queue-master-plan-2026-06-06)")
    parser.add_argument("--plan", required=True, help="Path to the plan markdown file")
    parser.add_argument("--goal", default="", help="One-sentence council goal")
    parser.add_argument("--key-finding", default="", help="Most important research insight")
    parser.add_argument("--pattern", default="", help="Relevant prior council pattern")
    parser.add_argument("--planner", default="daen", help="Planner agent for revision dispatch (default: daen)")
    parser.add_argument("--deadline", type=int, default=2, help="Planner response deadline in hours (default: 2)")
    parser.add_argument("--reviewers", nargs="+",
                        default=["socrates", "nemesis", "cassandra", "confucius", "wonhoo"],
                        help="Reviewer agents (default: all 5)")
    args = parser.parse_args()

    COUNCILS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"CONVENE: {args.slug}")
    print(f"Plan: {args.plan}")
    print(f"{'='*60}")

    # Check slug format — no spaces
    if " " in args.slug:
        print("ERROR: Slug must not contain spaces", file=sys.stderr)
        sys.exit(1)

    # Check for existing manifest
    manifest_path = COUNCILS_DIR / f"{args.slug}.json"
    if manifest_path.exists():
        print(f"WARNING: Manifest already exists at {manifest_path}")
        print("Use a different slug or archive the existing one first.")
        sys.exit(1)

    # Validate plan (fail noisily on bad input)
    print("Validating plan...")
    plan_hash = validate_plan(args.plan)
    print(f"  Plan exists, readable, OK.")

    # Build manifest
    print("Building manifest...")
    manifest = build_manifest(
        args.slug, args.plan, plan_hash,
        args.reviewers, args.planner, args.deadline
    )
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"  Manifest written: {manifest_path}")

    # Dispatch round 1
    print("Dispatching round 1...")
    dispatch_round1(args.slug, args.plan, plan_hash,
                    args.reviewers, args.goal, args.key_finding, args.pattern)

    # Hardening (2026-06-18): self-heal the tick engine. A convened council is dead
    # if council-tick.timer is off — that silent disable was the root cause of the
    # 2026-06-18 council stall. Every convene re-guarantees the engine is live.
    print("Ensuring council-tick engine is live...")
    subprocess.run(["systemctl", "enable", "--now", "council-tick.timer"], capture_output=True)
    _en = subprocess.run(["systemctl", "is-enabled", "council-tick.timer"],
                         capture_output=True, text=True).stdout.strip()
    print(f"  council-tick.timer: {_en or 'unknown'}")

    print(f"\n✅ Council convened. The cron tick driver will handle subsequent rounds.")
    print(f"   Manifest: {manifest_path}")
    print(f"   Planner: {args.planner} (deadline: {args.deadline}h)")
    print(f"   Next: monitor via council_tick.py --all or wait for cron")


if __name__ == "__main__":
    import os  # needed for os.access
    main()
