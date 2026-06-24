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
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

COUNCILS_DIR = Path("/opt/gcg/shared/councils")
REVIEWERS = ["SOCRATES", "NEMESIS", "CASSANDRA", "CONFUCIUS", "WONHOO"]

# Routes blocked-gate alerts to Peter via daen's inbox poller + Telegram.
GUARDIAN_AGENT = "daen"

# Patterns that match "## Internal findings" / "**Internal findings**" etc.
_RE_INTERNAL = re.compile(r'(?im)^(?:#+\s*|[\*_]+)internal\s+findings')
_RE_EXTERNAL = re.compile(r'(?im)^(?:#+\s*|[\*_]+)external\s+findings')


def check_research_gate(slug: str, plan_path: str,
                        research_artifact: str = "") -> tuple[bool, str]:
    """Return (ok, detail).

    Checks that a research artifact with BOTH an 'Internal findings' block
    AND an 'External findings' block exists before reviewers are dispatched.
    Searches (in order):
      1. Explicit --research-artifact path (if supplied)
      2. The plan file itself
      3. <plan-dir>/*research*.md  (any file in the plan's directory)
      4. /opt/gcg/shared/council-inbox/<slug>-research.md
      5. /opt/gcg/shared/plans/<slug>-research.md
    """
    plan_dir = Path(plan_path).parent

    candidates: list[Path] = []
    if research_artifact:
        candidates.append(Path(research_artifact))
    candidates.append(Path(plan_path))
    candidates += sorted(plan_dir.glob("*research*.md"))
    candidates.append(Path("/opt/gcg/shared/council-inbox") / f"{slug}-research.md")
    candidates.append(Path("/opt/gcg/shared/plans") / f"{slug}-research.md")

    for f in candidates:
        if not f.exists() or not f.is_file():
            continue
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        has_internal = bool(_RE_INTERNAL.search(text))
        has_external = bool(_RE_EXTERNAL.search(text))
        if has_internal and has_external:
            return True, str(f)
        # File has one block but not both — it's a partial research artifact.
        # Stop here and report what's missing (don't silently skip to the next
        # candidate, or a half-done research file would be masked by a sidecar).
        if has_internal or has_external:
            missing = []
            if not has_internal:
                missing.append("Internal findings")
            if not has_external:
                missing.append("External findings")
            return False, f"found {f.name} but missing: {', '.join(missing)}"
        # Neither block in this file — not the research artifact; keep looking.

    return False, "no research artifact found (need ## Internal findings + ## External findings)"
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
        f"1b. APPLY THE PONYTAIL LENS (YAGNI / least-code, MANDATORY every review): hunt over-engineering — unnecessary mechanisms, speculative abstractions, 'might-need-later', anything reuse/delete beats building. Invoke the ponytail skill if installed.\n"
            f"2. READ workspace/memory/review-log.md — scan ALL past entries for patterns applicable to this plan.\n"
            f"3. PICK UP HISTORY (prevents re-running councils + re-finding old bugs): "
            f"ls /opt/gcg/shared/plans/reviews/ and READ prior verdicts for RELATED plans "
            f"(match the slug stem and *inbox* *a2a* *spine* *cascade* *orchestrator*), "
            f"plus your own earlier verdict for this slug if one exists. "
            f"Engage with bugs ALREADY found — if this plan repeats a previously-flagged mistake, "
            f"say so explicitly and cite the prior verdict. Do NOT re-derive from scratch.\n"
            f"4. REVIEW the plan against your role. Read the actual plan file at: {plan_path}\n"
            f"5. WRITE verdict to the ABSOLUTE path {vf_path} "
            f"(use this EXACT absolute path — do NOT write to a workspace-relative path; "
            f"the loop reads only this location) using EXACTLY this format:\n\n"
            f"## Verdict\n"
            f"**VERDICT: [PASS|FAIL|CONDITIONAL]**\n\n"
            f"### Prior patterns applied\n"
            f"- [Pattern from your review-log if applicable, else 'None applicable']\n\n"
            f"### Key findings\n"
            f"- [Finding 1]\n"
            f"- [Finding 2]\n\n"
            f"### Ponytail cuts (MANDATORY — YAGNI/least-code: what to delete or simplify; reuse beats build; write 'None — already minimal' only if truly none)\n"
        f"- [Over-engineered element \u2192 simpler/reuse alternative, or 'None']\n\n"
        f"### Required changes (FAIL/CONDITIONAL only)\n"
            f"- [ROOT CAUSE + specific change required before PASS — not a symptom fix]\n\n"
            f"6. EMBED to pgvector: ...\n"
            f"7. APPEND hash footer:\n"
            f"   reviewed-hash: {plan_hash}\n"
            f"8. CLOSE inbox: fleet done <message_id> && rm workspace/inbox/<message_id>.json"
        )

        subprocess.run(["fleet", "send", agent_id, msg], check=True,
                       env={**os.environ, "FLEET_SKIP_CASCADE_GUARD": "1"})
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
    parser.add_argument("--research-artifact", default="",
                        help="Explicit path to research artifact (overrides auto-search)")
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

    # Research gate (SKILL.md Step 1 — hard gate before reviewers dispatch)
    print("Checking research gate...")
    gate_ok, gate_detail = check_research_gate(
        args.slug, args.plan, getattr(args, "research_artifact", "")
    )
    if not gate_ok:
        block_msg = (
            f"COUNCIL {args.slug} blocked: research gate ({gate_detail}). "
            f"Write a research artifact with both '## Internal findings' and "
            f"'## External findings' blocks, then re-convene."
        )
        print(f"\nERROR: {block_msg}", file=sys.stderr)
        subprocess.run(
            ["fleet", "send", "--priority", "2", GUARDIAN_AGENT, block_msg],
            check=False, env={**os.environ, "FLEET_SKIP_CASCADE_GUARD": "1"},
        )
        sys.exit(2)
    print(f"  Research gate OK — artifact: {gate_detail}")

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
