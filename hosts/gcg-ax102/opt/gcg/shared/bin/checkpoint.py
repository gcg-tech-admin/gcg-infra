#!/usr/bin/env python3
"""
checkpoint.py — Append-only JSONL event-log + checkpoint/resume for multi-step runs.

Zero new infra required (no DB table needed for the pure-JSONL path).
DB-backed mode uses `run_checkpoints` table when available.

Usage:
    from checkpoint import CheckpointRun, CheckpointStep

    run = CheckpointRun("council:my-plan", run_type="council",
                        context={"plan_path": "/opt/gcg/shared/plans/my-plan.md"})

    for reviewer in ["SOCRATES", "NEMESIS", "CASSANDRA", "CONFUCIUS", "WONHOO"]:
        step = run.begin_step("dispatch", reviewer=reviewer)
        # ... dispatch reviewer ...
        step.complete(status="ok", payload={"message_id": 12345})

    # If this process dies here, resume will skip the dispatched reviewers.

Author: Nik | Date: 2026-06-26
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

LOG_DIR = Path("/opt/gcg/shared/run-logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CheckpointRun:
    """Manages a checkpointed multi-step run with an append-only JSONL event log."""

    def __init__(self, run_id: str, run_type: str = "generic",
                 context: Optional[dict] = None, db_sync: bool = False):
        self.run_id = run_id
        self.run_type = run_type
        self.context = context or {}
        self.db_sync = db_sync
        self.log_path = LOG_DIR / f"{run_id}.jsonl"
        self._last_index = 0
        self._resume_count = 0

        # Load existing state if resuming
        if self.log_path.exists():
            self._load_existing()
            self._resume_count = self._count_resume()

        # Sync to DB if enabled
        if db_sync:
            self._db_upsert()

    def _log_path(self) -> Path:
        return LOG_DIR / f"{self.run_id}.jsonl"

    def _load_existing(self):
        """Replay the event log to find last_good_index."""
        if not self.log_path.exists():
            return
        max_idx = 0
        with open(self.log_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    idx = event.get("event_index", 0)
                    if event.get("status") == "ok" and idx > max_idx:
                        max_idx = idx
                except json.JSONDecodeError:
                    continue
        self._last_index = max_idx

    def _count_resume(self) -> int:
        """Count how many events indicate a resume marker."""
        count = 0
        if not self.log_path.exists():
            return 0
        with open(self.log_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    if '"step":"resume_marker"' in line:
                        count += 1
                except Exception:
                    continue
        return count

    def last_good_index(self) -> int:
        return self._last_index

    def is_resuming(self) -> bool:
        return self._last_index > 0

    def resume_count(self) -> int:
        return self._resume_count

    def mark_resume(self):
        """Write a resume marker event. Called when resuming from a previous crash."""
        self._resume_count += 1
        self._append_event({
            "event_index": self._last_index + 1,  # placeholder, will be re-indexed
            "step": "resume_marker",
            "status": "ok",
            "ts": _now_iso(),
            "payload": {
                "resumed_from_index": self._last_index,
                "resume_count": self._resume_count,
            }
        })
        # Don't advance last_good_index for markers — they're metadata

    def begin_step(self, step: str, reviewer: Optional[str] = None,
                   payload: Optional[dict] = None) -> "CheckpointStep":
        """Begin a new logical step. Returns a CheckpointStep context manager."""
        self._last_index += 1
        return CheckpointStep(self, self._last_index, step, reviewer, payload)

    def _append_event(self, event: dict):
        """Append a single JSON line to the event log."""
        with open(self.log_path, "a") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        if self.db_sync:
            self._db_upsert()

    def _db_upsert(self):
        """Sync state to run_checkpoints table."""
        try:
            import sys
            sys.path.insert(0, "/opt/gcg/shared")
            from db_config import get_connection
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO run_checkpoints (run_id, run_type, status, last_good_index,
                                             total_steps, event_log_path, context, resume_count)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    last_good_index = EXCLUDED.last_good_index,
                    total_steps = EXCLUDED.total_steps,
                    context = EXCLUDED.context,
                    updated_at = now(),
                    resume_count = EXCLUDED.resume_count
            """, (
                self.run_id, self.run_type,
                "running" if self._last_index > 0 else "running",
                self._last_index, self._last_index,
                str(self.log_path), json.dumps(self.context), self._resume_count,
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            # DB sync is best-effort for now — JSONL is the source of truth
            print(f"[checkpoint] DB sync warning: {e}")

    def mark_completed(self):
        """Mark the run as completed."""
        self._append_event({
            "event_index": self._last_index + 1,
            "step": "run_completed",
            "status": "ok",
            "ts": _now_iso(),
            "payload": {"total_steps": self._last_index}
        })
        if self.db_sync:
            try:
                import sys
                sys.path.insert(0, "/opt/gcg/shared")
                from db_config import get_connection
                conn = get_connection()
                cur = conn.cursor()
                cur.execute("""
                    UPDATE run_checkpoints SET status='completed', completed_at=now(),
                    updated_at=now(), total_steps=%s
                    WHERE run_id=%s
                """, (self._last_index, self.run_id))
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"[checkpoint] DB sync warning: {e}")

    def mark_failed(self, error: str):
        """Mark the run as failed."""
        self._append_event({
            "event_index": self._last_index + 1,
            "step": "run_failed",
            "status": "error",
            "ts": _now_iso(),
            "payload": {"error": error}
        })
        if self.db_sync:
            try:
                import sys
                sys.path.insert(0, "/opt/gcg/shared")
                from db_config import get_connection
                conn = get_connection()
                cur = conn.cursor()
                cur.execute("""
                    UPDATE run_checkpoints SET status='failed', updated_at=now()
                    WHERE run_id=%s
                """, (self.run_id,))
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"[checkpoint] DB sync warning: {e}")


class CheckpointStep:
    """A single checkpointed step within a run. Use as context manager."""

    def __init__(self, run: CheckpointRun, index: int, step: str,
                 reviewer: Optional[str] = None, payload: Optional[dict] = None):
        self.run = run
        self.index = index
        self.step = step
        self.reviewer = reviewer
        self.payload = payload or {}
        self._completed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None and not self._completed:
            # Auto-complete if no exception and not explicitly completed
            self.complete("ok")

    def complete(self, status: str = "ok", payload: Optional[dict] = None,
                 error: Optional[str] = None):
        """Mark this step as completed and append the event."""
        event = {
            "event_index": self.index,
            "step": self.step,
            "status": status,
            "ts": _now_iso(),
            "payload": payload or self.payload,
        }
        if self.reviewer:
            event["reviewer"] = self.reviewer
        if error:
            event["error"] = error

        self.run._append_event(event)
        self._completed = True


# ── Council-specific helpers ────────────────────────────────────────────────

def council_run_id(slug: str, round_n: int) -> str:
    """Generate a canonical run_id for a council round."""
    return f"council:{slug}:R{round_n}"


def get_council_checkpoint(slug: str, round_n: int) -> Optional[CheckpointRun]:
    """Get an existing checkpoint for a council round, or None."""
    run_id = council_run_id(slug, round_n)
    log_path = LOG_DIR / f"{run_id}.jsonl"
    if log_path.exists():
        return CheckpointRun(run_id, run_type="council")
    return None


def checkpoints_for_slug(slug: str) -> list[CheckpointRun]:
    """Get all checkpoint runs for a council slug (all rounds)."""
    runs = []
    for log_file in sorted(LOG_DIR.glob(f"council:{slug}:R*.jsonl")):
        run_id = log_file.stem
        runs.append(CheckpointRun(run_id, run_type="council"))
    return runs


# ── Self-test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile

    print("=== Checkpoint Self-Test ===\n")

    # Test 1: Basic run with checkpointing
    run_id = f"test:basic:{int(time.time())}"
    run = CheckpointRun(run_id, run_type="test",
                        context={"purpose": "self-test"})

    reviewers = ["ALPHA", "BETA", "GAMMA"]
    for r in reviewers:
        with run.begin_step("dispatch", reviewer=r) as step:
            step.complete(status="ok", payload={"dispatched_to": r.lower()})
            print(f"  ✓ Dispatched {r} (event_index={step.index})")

    assert run.last_good_index() == 3, f"Expected 3, got {run.last_good_index()}"
    print(f"\n  Last good index: {run.last_good_index()} ✓")

    # Test 2: Simulate crash + resume
    print("\n--- Simulating crash after 2 of 3 verdicts ---")
    run_id2 = f"test:resume:{int(time.time())}"
    run2 = CheckpointRun(run_id2, run_type="test",
                         context={"test": "resume"})

    # Do 2 steps
    with run2.begin_step("collect_verdict", reviewer="ALPHA") as step:
        step.complete("ok", {"verdict": "PASS"})
        print(f"  ✓ Collected ALPHA verdict")
    with run2.begin_step("collect_verdict", reviewer="BETA") as step:
        step.complete("ok", {"verdict": "FAIL"})
        print(f"  ✓ Collected BETA verdict")

    assert run2.last_good_index() == 2

    # Now simulate resume — create a NEW CheckpointRun with same run_id
    print("\n  --- Resuming from checkpoint ---")
    run2_resume = CheckpointRun(run_id2, run_type="test")
    assert run2_resume.is_resuming(), "Should detect resume"
    assert run2_resume.last_good_index() == 2, f"Expected 2, got {run2_resume.last_good_index()}"
    print(f"  ✓ Resumed at index {run2_resume.last_good_index()}")

    # Complete remaining step
    with run2_resume.begin_step("collect_verdict", reviewer="GAMMA") as step:
        step.complete("ok", {"verdict": "PASS"})
        print(f"  ✓ Collected GAMMA verdict (resumed)")

    assert run2_resume.last_good_index() == 3, f"Expected 3, got {run2_resume.last_good_index()}"

    run2_resume.mark_completed()
    print(f"\n  ✓ Resume test passed — final index: {run2_resume.last_good_index()}")

    # Test 3: Council run_id helper
    rid = council_run_id("my-plan", 2)
    assert rid == "council:my-plan:R2", f"Expected council:my-plan:R2, got {rid}"
    print(f"\n  ✓ Council run_id format: {rid}")

    # Cleanup
    for r_id in [run_id, run_id2]:
        p = LOG_DIR / f"{r_id}.jsonl"
        if p.exists():
            p.unlink()

    print("\n=== All tests passed ===")
