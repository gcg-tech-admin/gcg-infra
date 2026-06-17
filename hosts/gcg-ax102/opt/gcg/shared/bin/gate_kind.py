#!/usr/bin/env python3
"""
gate_kind.py — Gate state-machine for the fleet inbox pipeline (Phase 1.1).

Derives gate_kind (human_gate vs work_qa_gate) from task attributes.
Used by fleet CLI, gcg-inbox-poll, and the approve path to ensure
consistent gate behavior across all 34 agents.

Gate kinds:
  human_gate  — owner_type='human' OR task_type IN ('decision', 'revert_request')
                No agent-QA precondition. Resolved ONLY by human approver.
                Exempt from 30-min auto-fail (gentle 7d reminder only).
  work_qa_gate — qa_tier IN ('standard', 'high_stakes') AND NOT human_gate
                Auto-dispatched to QA agent. QA verdict required.
                high_stakes: human approve after QA pass.
                standard: auto-completes after QA pass.
  none        — trivial / no gate applies.

Usage:
  from gate_kind import derive_gate_kind
  kind = derive_gate_kind(owner_type='agent', task_type='standard', qa_tier='high_stakes')
  # => 'work_qa_gate'
"""

HUMAN_GATE_TASK_TYPES = frozenset({'decision', 'revert_request'})
WORK_GATE_QA_TIERS = frozenset({'standard', 'high_stakes'})


def derive_gate_kind(owner_type, task_type, qa_tier):
    """Return 'human_gate', 'work_qa_gate', or None.

    Args:
        owner_type: 'human' or 'agent' (or None)
        task_type: from agent_messages.task_type
        qa_tier: from agent_messages.qa_tier

    Returns:
        'human_gate'  — requires human approval, no QA auto-dispatch, exempt from auto-fail
        'work_qa_gate' — requires agent QA, auto-dispatched, auto-fail after 30min
        None          — trivial task, no gate
    """
    if owner_type == 'human' or task_type in HUMAN_GATE_TASK_TYPES:
        return 'human_gate'
    if qa_tier in WORK_GATE_QA_TIERS:
        return 'work_qa_gate'
    return None


def requires_qa_before_approval(gate_kind, qa_tier):
    """Check if QA is required before human approval.

    Rule: only work_qa_gate with high_stakes requires QA→approve sequence.
    human_gate tasks are directly approvable with no QA step.
    """
    if gate_kind == 'human_gate':
        return False
    if gate_kind == 'work_qa_gate' and qa_tier == 'high_stakes':
        return True
    return False


def is_exempt_from_auto_fail(gate_kind, qa_tier=None):
    """Check if this gate is exempt from the 30-min auto-fail timeout.

    human_gate: always exempt (human decisions can't be machine-timed).
    work_qa_gate + high_stakes: exempt after QA pass — Daen's approval is human-paced.
    work_qa_gate + standard: NOT exempt — QA pass auto-completes, no approval needed.
    """
    if gate_kind == 'human_gate':
        return True
    if gate_kind == 'work_qa_gate' and qa_tier == 'high_stakes':
        return True  # high_stakes approval gates are human-paced (Daen)
    return False


# ── CLI: derive gate_kind for a task by id ─────────────────────────
if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print("usage: gate_kind.py <owner_type> <task_type> <qa_tier>", file=sys.stderr)
        print("       gate_kind.py --task-id <id>  (reads from DB)", file=sys.stderr)
        sys.exit(2)

    if sys.argv[1] == '--task-id':
        import sys as _sys
        _sys.path.insert(0, '/opt/gcg/shared')
        from db_config import get_connection
        task_id = sys.argv[2]
        conn = get_connection(agent_name='talos')
        cur = conn.cursor()
        cur.execute(
            "SELECT owner_type, task_type, qa_tier FROM agent_messages WHERE id = %s",
            (task_id,)
        )
        row = cur.fetchone()
        cur.close(); conn.close()
        if not row:
            print(f"task {task_id} not found", file=sys.stderr)
            sys.exit(1)
        owner_type, task_type, qa_tier = row
    else:
        owner_type = sys.argv[1] if sys.argv[1] != 'None' else None
        task_type = sys.argv[2] if sys.argv[2] != 'None' else None
        qa_tier = sys.argv[3] if sys.argv[3] != 'None' else None

    kind = derive_gate_kind(owner_type, task_type, qa_tier)
    print(kind or 'none')
