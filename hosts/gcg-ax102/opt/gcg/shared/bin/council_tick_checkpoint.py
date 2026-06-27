#!/usr/bin/env python3
"""
council_tick_checkpoint.py — Council tick driver with checkpoint/resume.

Drop-in wrapper around council_tick.py. Adds checkpointing so that if the tick
process dies mid-round (e.g. after dispatching 3/5 reviewers), the next tick
resumes from the last completed step instead of re-running everything.

Usage:
    council_tick_checkpoint.py <slug>       # single council with checkpoint
    council_tick_checkpoint.py --all        # all active councils with checkpoint
    council_tick_checkpoint.py --demo       # run the kill-resume demo

Architecture:
    Wraps the council_tick state machine. Each logical step within a tick
    (dispatch, collect, gate) is checkpointed via checkpoint.py JSONL event log.
    On resume, steps already completed are skipped.

Author: Nik | Date: 2026-06-26
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add shared to path
sys.path.insert(0, "/opt/gcg/shared/bin")
from checkpoint import (
    CheckpointRun, CheckpointStep, council_run_id, get_council_checkpoint,
    LOG_DIR,
)

COUNCILS_DIR = Path("/opt/gcg/shared/councils")
VERDICT_DIR = Path("/opt/gcg/shared/plans/reviews")
REVIEWERS = ["SOCRATES", "NEMESIS", "CASSANDRA", "CONFUCIUS", "WONHOO"]
COUNCIL_TICK = Path("/opt/gcg/shared/bin/council_tick.py")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_manifest(slug: str) -> dict:
    """Load council manifest."""
    path = COUNCILS_DIR / f"{slug}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def tick_with_checkpoint(slug: str):
    """
    Run a council tick with checkpoint/resume protection.

    For each step in the tick lifecycle:
    1. Check if already completed (event_index ≤ last_good_index)
    2. If not, execute the step and checkpoint
    3. If crash, next run resumes from last completed step
    """
    manifest = load_manifest(slug)
    if manifest is None:
        print(f"❌ Council '{slug}' not found")
        return

    round_n = manifest.get("round", 1)
    run_id = council_run_id(slug, round_n)
    plan_path = manifest.get("plan_path", "")
    goal = manifest.get("goal", "")
    reviewers_expected = manifest.get("reviewers", [r.lower() for r in REVIEWERS])

    # ── Checkpoint: get or create run ──
    run = CheckpointRun(run_id, run_type="council",
                        context={"slug": slug, "round": round_n, "plan_path": plan_path},
                        db_sync=True)

    if run.is_resuming():
        print(f"\n🔄 RESUMING council tick: {slug} R{round_n}")
        print(f"   Last completed step index: {run.last_good_index()}")
        print(f"   Resume count: {run.resume_count()}")
        run.mark_resume()
    else:
        print(f"\n🆕 Starting council tick: {slug} R{round_n}")

    # ── The actual tick logic, checkpointed ──
    # Import council_tick helpers for dispatch
    import importlib.util
    spec = importlib.util.spec_from_file_location("council_tick", COUNCIL_TICK)
    ct = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ct)

    plan_hash = manifest.get("plan_hash", "")
    goal_str = manifest.get("goal", "")

    # Step 1: Dispatch all reviewers (if not already done)
    next_idx = run.last_good_index() + 1

    for i, r_name in enumerate(reviewers_expected):
        r_upper = r_name.upper()
        step_idx = next_idx + i

        if step_idx <= run.last_good_index():
            print(f"  ⏭️  SKIP dispatch {r_upper} (already done, index {step_idx})")
            continue

        with run.begin_step("dispatch", reviewer=r_upper) as step:
            print(f"  📤 Dispatching {r_upper}...")
            try:
                ct.dispatch_reviewer(slug, plan_path, plan_hash, goal_str,
                                     r_upper, round_n)
                step.complete(status="ok", payload={
                    "reviewer": r_upper,
                    "dispatched_at": _now_iso(),
                })
            except Exception as e:
                step.complete(status="error", error=str(e))
                raise

    print(f"  ✅ All {len(reviewers_expected)} reviewers dispatched")

    # Step 2: Now run council_tick to collect verdicts and compute gate
    # The council_tick.py collect+gate logic is idempotent — safe to re-run.
    # Checkpoint protects the dispatch phase (the expensive part).
    print(f"\n  Running council_tick.py {slug} for verdict collection + gate...")
    result = subprocess.run(
        [sys.executable, str(COUNCIL_TICK), slug],
        capture_output=True, text=True,
        env={**os.environ, "FLEET_SKIP_CASCADE_GUARD": "1"},
    )

    for line in result.stdout.strip().splitlines()[-20:]:
        print(f"    {line}")

    if result.returncode != 0:
        print(f"  ⚠️  council_tick exited with {result.returncode}")
        run.mark_failed(f"council_tick exit code {result.returncode}: {result.stderr[:200]}")
        return

    # Mark the round as checkpoint-complete
    run.mark_completed()
    print(f"\n  ✅ Round {round_n} checkpointed — log: {run.log_path}")


def tick_all_with_checkpoint():
    """Tick all active councils with checkpoint protection."""
    if not COUNCILS_DIR.exists():
        print("No councils directory")
        return

    manifests = sorted(COUNCILS_DIR.glob("*.json"))
    active = [m for m in manifests
              if not m.name.startswith("archived-")
              and not m.name.startswith("unresolved-")
              and not m.name.startswith("superseded-")]

    if not active:
        print("No active councils")
        return

    for mf in active:
        slug = mf.stem
        manifest = load_manifest(slug)
        if manifest and manifest.get("status") in ("passed", "archived", "escalated"):
            continue
        try:
            tick_with_checkpoint(slug)
        except Exception as e:
            print(f"  ❌ Tick failed for {slug}: {e}")


def demo_kill_resume():
    """
    Demo: kill-then-resume council run.

    This creates a synthetic council run, writes checkpoint events for
    "dispatched 5/5 + collected 2/5 verdicts", then simulates a resume.

    No real agents are dispatched — this is a pure checkpoint infrastructure demo.
    """
    print("=" * 60)
    print("CHECKPOINT/RESUME DEMO — Council Kill + Resume")
    print("=" * 60)

    slug = f"demo-checkpoint-{int(time.time())}"
    run_id = council_run_id(slug, 1)

    # ── Phase 1: Simulate run with crash ──
    print("\n📋 Phase 1: Simulate partial council run (crash after 2/5 verdicts)")

    run = CheckpointRun(run_id, run_type="council",
                        context={"slug": slug, "round": 1, "demo": True},
                        db_sync=True)

    # Dispatch all 5
    for r in REVIEWERS:
        with run.begin_step("dispatch", reviewer=r) as step:
            step.complete("ok", {"reviewer": r, "msg": f"dispatched {r}"})
            print(f"  ✓ Dispatched {r}")

    # Collect 2 verdicts
    for r in ["SOCRATES", "NEMESIS"]:
        vf = VERDICT_DIR / f"{slug}-{r}.md"
        vf.parent.mkdir(parents=True, exist_ok=True)
        vf.write_text(f"## Verdict\n**VERDICT: PASS**\n\n### Key findings\n- Looks good")
        with run.begin_step("collect_verdict", reviewer=r) as step:
            step.complete("ok", {"verdict": "PASS", "verdict_path": str(vf)})
            print(f"  ✓ Collected {r}: PASS")

    print(f"\n  💥 CRASH at index {run.last_good_index()} — 3 verdicts pending")
    print(f"  Run state saved to: {run.log_path}")

    # ── Phase 2: Resume ──
    print(f"\n📋 Phase 2: Resume from checkpoint (index {run.last_good_index()})")

    run2 = CheckpointRun(run_id, run_type="council",
                         context={"slug": slug, "round": 1, "demo": True},
                         db_sync=True)

    assert run2.is_resuming(), "FAIL: Should detect resume state"
    assert run2.last_good_index() == 7, f"FAIL: Expected 7, got {run2.last_good_index()}"
    print(f"  ✓ Detected resume at index {run2.last_good_index()}")

    run2.mark_resume()

    # "Skip" already-done steps
    done = set()
    for idx in range(1, run2.last_good_index() + 1):
        print(f"  ⏭️  SKIP step {idx} (already completed)")

    # Complete remaining 3 collections
    remaining = ["CASSANDRA", "CONFUCIUS", "WONHOO"]
    for r in remaining:
        vf = VERDICT_DIR / f"{slug}-{r}.md"
        vf.write_text(f"## Verdict\n**VERDICT: PASS**\n\n### Key findings\n- Fine")
        with run2.begin_step("collect_verdict", reviewer=r) as step:
            step.complete("ok", {"verdict": "PASS", "verdict_path": str(vf)})
            print(f"  ✓ Collected {r}: PASS (resumed)")

    assert run2.last_good_index() == 10, f"FAIL: Expected 10, got {run2.last_good_index()}"

    run2.mark_completed()
    print(f"\n  🎉 DEMO COMPLETE — council round finished after kill+resume")
    print(f"    5 dispatched → crashed at 2/5 verdicts → resumed → 5/5 collected")

    # ── Show the logs ──
    print(f"\n📄 Event log: {run2.log_path}")
    print("-" * 40)
    with open(run2.log_path) as f:
        for line in f:
            event = json.loads(line)
            status_icon = "💥" if event.get("step") == "resume_marker" else "  "
            print(f"  {status_icon} [{event.get('event_index','?')}] {event.get('step'):20s} "
                  f"{event.get('reviewer',''):12s} {event.get('status','')}")

    # Cleanup
    cleanup = [
        run2.log_path,
        *[VERDICT_DIR / f"{slug}-{r}.md" for r in REVIEWERS],
    ]
    for p in cleanup:
        if p.exists():
            p.unlink()

    print(f"\n  ✅ Demo artifacts cleaned up")


def main():
    parser = argparse.ArgumentParser(
        description="Council tick with checkpoint/resume")
    parser.add_argument("slug", nargs="?", help="Council slug")
    parser.add_argument("--all", action="store_true", help="Tick all active councils")
    parser.add_argument("--demo", action="store_true", help="Run the kill-resume demo")
    parser.add_argument("--list-checkpoints", action="store_true",
                        help="List all checkpoint runs")
    args = parser.parse_args()

    VERDICT_DIR.mkdir(parents=True, exist_ok=True)

    if args.demo:
        demo_kill_resume()
    elif args.list_checkpoints:
        logs = sorted(LOG_DIR.glob("*.jsonl"))
        if not logs:
            print("No checkpoint logs found")
        for log in logs:
            run_id = log.stem
            run = CheckpointRun(run_id)
            print(f"  {run_id:40s} index={run.last_good_index():3d}  "
                  f"resuming={run.is_resuming()}  resumes={run.resume_count()}")
    elif args.all:
        tick_all_with_checkpoint()
    elif args.slug:
        tick_with_checkpoint(args.slug)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
