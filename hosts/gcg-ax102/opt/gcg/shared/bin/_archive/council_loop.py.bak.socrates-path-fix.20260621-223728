#!/usr/bin/env python3
"""
GCG Council Consensus Loop — SELF-DRIVING
==========================================
Recursive council review until all 5 reviewers PASS.
No round limit — loops until full consensus or stuck-detection triggers escalation.

SELF-DRIVING: On round-complete with FAILs, the loop pushes a wake/ask to the
convener's OpenClaw session (via Phase-1 a2a wake/ask primitive, with cron-wake
fallback). The convener wakes, reads the REVISE request from its inbox, revises
the plan, and saves it — the loop auto-detects and re-dispatches. NO human prompt
required between rounds.

Stuck detection: if a reviewer returns IDENTICAL required changes for 2 consecutive rounds
with no revision touching their issue, they are stuck — escalate to Peter.

Usage:
    python3 council_loop.py --plan <slug> --plan-file <path> --goal "<goal>" [--key-finding "..."] [--pattern "..."] [--convener <agent>]

Outputs:
    /opt/gcg/shared/plans/reviews/<slug>-<REVIEWER>[-R<N>].md   verdict files
    /opt/gcg/shared/plans/reviews/<slug>-council-loop.json       loop state

Exit codes: 0=PASS/CONDITIONAL, 1=ESCALATE/ERROR
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

VERDICT_DIR = Path("/opt/gcg/shared/plans/reviews")
REVIEWERS = ["SOCRATES", "NEMESIS", "CASSANDRA", "CONFUCIUS", "WONHOO"]
POLL_INTERVAL = 30
POLL_TIMEOUT = 1800  # 30 min per round
REVISION_POLL_TIMEOUT = 600  # 10 min for convener revision

REVIEWER_IDS = {
    "SOCRATES": "socrates",
    "NEMESIS": "nemesis",
    "CASSANDRA": "cassandra",
    "CONFUCIUS": "confucius",
    "WONHOO": "wonhoo",
}

REVIEWER_ROLES = {
    "SOCRATES": "Question every assumption. Probe logic, expose contradictions, ask what the builder took for granted.",
    "NEMESIS": "Attack execution. Find what breaks under stress, what fails on agent 15 but not agent 1, what the rollback doesn't cover.",
    "CASSANDRA": "Project the future. What does this plan become in 3-6 months? Technical debt, scaling walls, lock-in, second-order effects.",
    "CONFUCIUS": "Verify every factual claim. Read the actual files. Check real configs. Confirm paths exist. Do NOT speculate — go look.",
    "WONHOO": "Practical feasibility. Will this actually work when a real person or agent executes it? Are steps clear? Timeline realistic? Simpler way?",
}

# ── Agent hooks config cache ──────────────────────────────────────────
_AGENT_HOOKS_CACHE: dict[str, dict] = {}


def _load_fleet_yaml() -> dict:
    """Load FLEET.yaml for agent port/token lookup."""
    fleet_yaml = Path("/opt/gcg/shared/FLEET.yaml")
    if not fleet_yaml.exists():
        return {}
    try:
        import yaml
        with open(fleet_yaml) as f:
            return yaml.safe_load(f)
    except ImportError:
        return {}
    except Exception:
        return {}


def _load_agent_hooks_config(agent: str) -> dict | None:
    """Load an agent's gateway URL and hooks token for wake calls.

    Returns dict with keys: url, token — or None if config not found.
    """
    if agent in _AGENT_HOOKS_CACHE:
        return _AGENT_HOOKS_CACHE[agent]

    result = _load_agent_hooks_config_inner(agent)
    _AGENT_HOOKS_CACHE[agent] = result
    return result


def _load_agent_hooks_config_inner(agent: str) -> dict | None:
    # Path 1: FLEET.yaml
    fleet = _load_fleet_yaml()
    agents = fleet.get("agents", {})
    agent_cfg = agents.get(agent, {})
    port = agent_cfg.get("port")
    workspace = agent_cfg.get("workspace")

    # Path 2: Direct openclaw.json read (same host — all agents on AX102)
    config_path = Path(f"/opt/gcg/openclaw-{agent}/openclaw.json")
    if config_path.exists():
        try:
            with open(config_path) as f:
                cfg = json.load(f)
            hooks = cfg.get("hooks", {})
            token = hooks.get("token", "")
            gateway = cfg.get("gateway", {})
            port = gateway.get("port", port)
            if token and port:
                return {"url": f"http://127.0.0.1:{port}", "token": token}
        except Exception:
            pass

    # Path 3: FLEET.yaml port only — try gateway token from hooks_token file
    if port and workspace:
        # Try extracting token from credentials dir
        cred_path = Path("/opt/gcg/shared/credentials")
        for fname in [f"gateway_token_{agent}.txt", f"hooks_token_{agent}.txt"]:
            tf = cred_path / fname
            if tf.exists():
                token = tf.read_text().strip()
                if token:
                    return {"url": f"http://127.0.0.1:{port}", "token": token}

    return None


def push_revise_to_convener(agent: str, message: str, priority: int = 3) -> bool:
    """PUSH a wake/ask to the convener's OpenClaw session (self-driving loop).

    Uses the Phase-1 a2a wake/ask primitive verified on 2026-06-16:
    a `fleet send` row on the agent_messages bus is delivered by the convener's
    `gcg-inbox-poll@<agent>.service`, which WAKES the convener's session. The
    message IS the wake — there is no separate `fleet wake` command on this host
    (`fleet send` already triggers the recipient's run; see AGENTS.md / FLEET_INBOX).

    So this single `fleet send` both ASKS (delivers verdicts + 'revise FAILs +
    relaunch') and WAKES the convener. The convener's inbox handler revises the
    plan and `fleet reply`s; the loop auto-detects the revised file.

      - PRIMARY (Phase-1 primitive): fleet send <agent> "<revise msg>" → poller wake
      - INTERIM FALLBACK (cron-wake): HTTP POST to the convener's /hooks/wake
        endpoint, for an immediate poke if the bus send fails.

    Returns True if the push was delivered.
    """
    # ── Phase-1 a2a wake/ask primitive: fleet send → poller wakes the session ──
    try:
        result = subprocess.run(
            ["fleet", "send", "--priority", str(priority), agent, message],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            print(f"  ⚡ Revise ask pushed via fleet send → {agent} (poller wakes session)")
            return True
        print(f"  ⚠ fleet send returned {result.returncode}: {result.stderr.strip()[:200]}")
    except FileNotFoundError:
        print(f"  ⚠ fleet CLI not found on PATH")
    except subprocess.TimeoutExpired:
        print(f"  ⚠ fleet send timed out")

    # ── Interim cron-wake fallback: gateway hooks API (immediate poke) ──
    hooks_cfg = _load_agent_hooks_config(agent)
    if not hooks_cfg:
        print(f"  ⚠ Cannot push to {agent}: fleet send failed and no hooks config found")
        return False

    wake_url = f"{hooks_cfg['url']}/hooks/wake"
    wake_payload = json.dumps({
        "text": f"[COUNCIL] {message[:500]}",
        "mode": "now"
    }).encode("utf-8")

    req = urllib.request.Request(
        wake_url,
        data=wake_payload,
        headers={
            "Authorization": f"Bearer {hooks_cfg['token']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
            if body.get("ok"):
                print(f"  ⚡ Revise ask pushed via hooks API (fallback) → {agent}")
                return True
            print(f"  ⚠ Wake hooks returned: {body}")
    except urllib.error.HTTPError as e:
        print(f"  ⚠ Wake hooks HTTP {e.code}: {e.reason}")
    except Exception as e:
        print(f"  ⚠ Wake hooks failed: {e}")

    return False


def verdict_file(plan_slug: str, reviewer: str, round_n: int) -> Path:
    if round_n == 1:
        return VERDICT_DIR / f"{plan_slug}-{reviewer}.md"
    return VERDICT_DIR / f"{plan_slug}-{reviewer}-R{round_n}.md"


def parse_verdict(path: Path) -> str:
    """Extract PASS/FAIL/CONDITIONAL from verdict file."""
    try:
        text = path.read_text()
        for pattern in [
            r"\*\*VERDICT:\s*(PASS|FAIL|CONDITIONAL)\*\*",
            r"VERDICT:\s*(PASS|FAIL|CONDITIONAL)",
            r"\*\*Verdict[:\s]*\*\*\s*(PASS|FAIL|CONDITIONAL)",
            r"^(PASS|FAIL|CONDITIONAL)\b",
            r"## Verdict\s*\n\s*(PASS|FAIL|CONDITIONAL)",
        ]:
            m = re.search(pattern, text, re.MULTILINE | re.IGNORECASE)
            if m:
                return m.group(1).upper()
    except Exception:
        pass
    return "UNKNOWN"


def extract_required_changes(path: Path) -> str:
    """Extract 'Required changes' section from a FAIL verdict."""
    try:
        text = path.read_text()
        m = re.search(
            r"###\s*Required changes[^\n]*\n(.*?)(?=\n###|\n##|\Z)",
            text, re.DOTALL | re.IGNORECASE
        )
        if m:
            return m.group(1).strip()[:800]
    except Exception:
        pass
    return "(see verdict file)"


def normalize_changes(text: str) -> str:
    """Normalize required changes text for stuck detection comparison."""
    return re.sub(r'\s+', ' ', text.strip().lower())


def dispatch_reviewer(
    plan_slug: str, plan_file: str, goal: str,
    reviewer: str, round_n: int,
    key_finding: str = "", pattern: str = "", prior_fail: str = "",
    changed_since: str = ""
):
    agent_id = REVIEWER_IDS[reviewer]
    role = REVIEWER_ROLES[reviewer]
    vf = verdict_file(plan_slug, reviewer, round_n)

    round_header = f"Round {round_n}"
    revision_ctx = ""
    if round_n > 1:
        changed_note = ""
        if changed_since:
            changed_note = f"\nCHANGED-SINCE R{round_n-1}: {changed_since}\n"
        revision_ctx = (
            f"\n\n⚠️ REVISION ROUND {round_n}: Plan has been revised since your last review."
            f"\nRevised plan path: {plan_file}" + changed_note +
            f"\nYour prior FAIL required:\n{prior_fail}"
            f"\nFocus ONLY on: (1) your previously flagged issues, (2) any sections touched by the revision."
            f"\n\nROOT CAUSE CHECK: Your required changes must target the ROOT CAUSE, not symptoms."
            f" If the revision addressed your root cause (even imperfectly), engage with what remains."
            f" If the revision did not touch your root cause at all, say so explicitly."
        )

    msg = (
        f"COUNCIL REVIEW REQUEST — {plan_slug} ({round_header})\n\n"
        f"Goal: {goal} | Plan: {plan_file}"
        + (f" | Key finding: {key_finding}" if key_finding else "")
        + (f" | Prior pattern: {pattern}" if pattern else "")
        + revision_ctx + "\n\n"
        f"Your role: {role}\n\n"
        f"MANDATORY SEQUENCE:\n"
        f"1. READ workspace/SOUL.md — your identity and review principles.\n"
        f"2. READ workspace/memory/review-log.md — scan ALL past entries for patterns applicable to this plan. "
        f"You MUST list applicable patterns in your verdict. 'None applicable' is a valid answer but must be explicit.\n"
        f"3. REVIEW the plan against your role. Read the actual plan file at: {plan_file}\n"
        f"4. WRITE verdict to {vf} using EXACTLY this format:\n\n"
        f"## Verdict\n"
        f"**VERDICT: [PASS|FAIL|CONDITIONAL]**\n\n"
        f"### Prior patterns applied\n"
        f"- [Pattern from your review-log if applicable, else 'None applicable']\n\n"
        f"### Key findings\n"
        f"- [Finding 1]\n"
        f"- [Finding 2]\n\n"
        f"### Required changes (FAIL/CONDITIONAL only)\n"
        f"- [ROOT CAUSE + specific change required before PASS — not a symptom fix]\n\n"
        f"5. EMBED to pgvector:\n"
        f"   /opt/gcg/shared/bin/memory capture --agent {agent_id} --memory-type lesson "
        f"--importance high --scope agent_private "
        f"\"Council review {plan_slug} R{round_n} {datetime.now().strftime('%Y-%m-%d')}. "
        f"Verdict: [VERDICT]. Key findings: [summary]. New patterns: [any].\"\n"
        f"6. CLOSE inbox: fleet done <message_id> && rm workspace/inbox/<message_id>.json"
    )

    # Bypass the Cascade-First guard: reviewer prompts legitimately contain "?" + review verbs.
    subprocess.run(["fleet", "send", agent_id, msg], check=True,
                   env={**os.environ, "FLEET_SKIP_CASCADE_GUARD": "1"})
    print(f"  → {reviewer}: dispatched (round {round_n})")


def poll_verdicts(plan_slug: str, reviewers: list, round_n: int) -> dict:
    """Poll until all reviewers in list write verdict files. Returns {reviewer: verdict}."""
    start = time.time()
    results = {}
    while time.time() - start < POLL_TIMEOUT:
        for r in reviewers:
            if r not in results:
                vf = verdict_file(plan_slug, r, round_n)
                if vf.exists():
                    verdict = parse_verdict(vf)
                    results[r] = verdict
                    print(f"  ✓ {r}: {verdict}")
        if len(results) == len(reviewers):
            break
        remaining = [r for r in reviewers if r not in results]
        elapsed = int(time.time() - start)
        print(f"  [{elapsed}s] Waiting... {len(results)}/{len(reviewers)} received. Pending: {remaining}")
        time.sleep(POLL_INTERVAL)

    for r in reviewers:
        if r not in results:
            results[r] = "TIMEOUT"
            print(f"  ✗ {r}: TIMEOUT")
    return results


def run_loop(
    plan_slug: str, plan_file: str, goal: str,
    key_finding: str = "", pattern: str = "",
    convener: str = "daen"
) -> str:
    VERDICT_DIR.mkdir(parents=True, exist_ok=True)

    state_file = VERDICT_DIR / f"{plan_slug}-council-loop.json"
    state = {
        "plan_slug": plan_slug,
        "plan_file": plan_file,
        "goal": goal,
        "started": datetime.now().isoformat(),
        "rounds": [],
        "final_gate": None,
        "convener": convener,
    }

    active_reviewers = list(REVIEWERS)
    fail_notes: dict[str, str] = {}
    prev_fail_notes: dict[str, str] = {}
    stuck_rounds: dict[str, int] = {}
    round_n = 0

    while True:
        round_n += 1
        print(f"\n{'='*60}")
        print(f"COUNCIL ROUND {round_n} — {len(active_reviewers)} reviewer(s)")
        print(f"Plan: {plan_file}")
        print(f"Goal: {goal}")
        print(f"Convener: {convener}")
        print(f"{'='*60}")

        for r in active_reviewers:
            dispatch_reviewer(plan_slug, plan_file, goal, r, round_n,
                              key_finding, pattern, fail_notes.get(r, ""),
                              changed_since=state.get("changed_since", ""))

        print(f"\nPolling for {len(active_reviewers)} verdict(s) (timeout {POLL_TIMEOUT//60}m)...")
        verdicts = poll_verdicts(plan_slug, active_reviewers, round_n)

        round_state = {"round": round_n, "active_reviewers": active_reviewers, "verdicts": verdicts}
        state["rounds"].append(round_state)
        state_file.write_text(json.dumps(state, indent=2))

        fails = [r for r, v in verdicts.items() if v in ("FAIL", "TIMEOUT", "UNKNOWN")]
        conditionals = [r for r, v in verdicts.items() if v == "CONDITIONAL"]
        passes = [r for r, v in verdicts.items() if v == "PASS"]

        print(f"\nRound {round_n} results:")
        if passes:
            print(f"  PASS         : {passes}")
        if conditionals:
            print(f"  CONDITIONAL  : {conditionals}")
        if fails:
            print(f"  FAIL/TIMEOUT : {fails}")

        # ── GATE: Full consensus ──
        if not fails:
            if not conditionals:
                state["final_gate"] = "PASS"
                state_file.write_text(json.dumps(state, indent=2))
                print(f"\n✅ FULL CONSENSUS — ALL PASS (round {round_n})")
                print(f"State: {state_file}")
                return "PASS"
            else:
                state["final_gate"] = "CONDITIONAL"
                state["conditional_reviewers"] = conditionals
                state_file.write_text(json.dumps(state, indent=2))
                print(f"\n⚠️  CONDITIONAL CONSENSUS — {conditionals}")
                print(f"All remaining reviewers returned CONDITIONAL. Check required changes for consistency.")
                print(f"State: {state_file}")
                return "CONDITIONAL"

        # ── Stuck detection ──
        for r in fails:
            vf = verdict_file(plan_slug, r, round_n)
            current_changes = extract_required_changes(vf)
            current_normalized = normalize_changes(current_changes)
            prev_normalized = normalize_changes(prev_fail_notes.get(r, ""))

            if r in prev_fail_notes and current_normalized == prev_normalized and current_normalized not in ("", "(see verdict file)"):
                stuck_rounds[r] = stuck_rounds.get(r, 0) + 1
            else:
                stuck_rounds[r] = 0

            fail_notes[r] = current_changes

        prev_fail_notes = {r: fail_notes[r] for r in fails}

        stuck = [r for r in fails if stuck_rounds.get(r, 0) >= 1]
        if stuck:
            state["final_gate"] = "ESCALATE"
            state["escalate_reason"] = (
                f"Stuck at round {round_n}: reviewers {stuck} returned identical required changes "
                f"for 2 consecutive rounds. Convener is not addressing root cause."
            )
            state_file.write_text(json.dumps(state, indent=2))
            print(f"\n🚨 STUCK — ESCALATE TO PETER")
            print(f"These reviewers returned the same required changes 2 rounds in a row:")
            for r in stuck:
                vf = verdict_file(plan_slug, r, round_n)
                print(f"\n  [{r}] Unchanged required changes:\n    {fail_notes[r]}")
            print(f"\nDiagnosis: convener is revising around the issue, not fixing the root cause.")
            print(f"Action: Peter must review the stuck feedback and decide: fix root cause or override reviewer.")
            print(f"\nState: {state_file}")
            return "ESCALATE"

        # ── FAILs but not stuck → REVISE via convener ──
        print(f"\n{'─'*60}")
        print(f"🔄 REVISION NEEDED before Round {round_n + 1}")
        print(f"Failing reviewers: {fails}")

        revised = plan_file.replace(".md", f"-R{round_n + 1}.md")

        # Collect required changes
        changes_summary = []
        changed_parts = []
        for r in fails:
            print(f"\n  [{r}] Required changes (ROOT CAUSE):\n    {fail_notes[r]}")
            changes_summary.append(f"[{r}]\n{fail_notes[r]}")
            changed_parts.append(f"{r}: {normalize_changes(fail_notes[r])[:150]}")
        changed_since = " | ".join(changed_parts)

        # ── SELF-DRIVING PUSH: one fleet-send that ASKS (verdicts + revise+relaunch)
        #    AND WAKES the convener's session via its inbox poller (Phase-1 primitive).
        #    No stdin pause — the loop drives itself between rounds. ──
        revise_msg = (
            f"COUNCIL REVISE REQUEST — {plan_slug} (Round {round_n}→{round_n + 1})\n\n"
            f"Round {round_n} complete. FAILing reviewers: {', '.join(fails)}.\n"
            f"Current plan: {plan_file}\n"
            f"Target revised path: {revised}\n"
            f"Goal: {goal}\n\n"
            f"Failing reviewers — required changes (ROOT CAUSE, not symptoms):\n\n"
            + "\n\n".join(changes_summary) + "\n\n"
            f"ACTION (convener self-driving handler): Read the current plan at {plan_file}. "
            f"Revise ONLY the FAILing sections to address the ROOT CAUSES above (not symptoms). "
            f"Write the revised plan to: {revised}\n"
            f"Then: fleet reply <this_msg_id> \"Revised plan written to {revised}\"\n"
            f"The loop auto-detects {revised} and re-dispatches ONLY the FAILers — "
            f"it relaunches itself. Stop only on full PASS or stuck-escalation. "
            f"No human prompt required."
        )
        pushed = push_revise_to_convener(convener, revise_msg, priority=3)
        if pushed:
            print(f"  → REVISE ask pushed to {convener} (session woken; self-driving)")
        else:
            print(f"  ⚠ Could not push REVISE ask to {convener} — will still poll for revised plan")

        # ── Poll for revised plan (convener writes it after being woken) ──
        revision_poll_start = time.time()
        revised_found = False
        print(f"  Polling for revised plan: {revised} (timeout {REVISION_POLL_TIMEOUT}s)...")
        while time.time() - revision_poll_start < REVISION_POLL_TIMEOUT:
            if os.path.exists(revised):
                revised_found = True
                break
            time.sleep(10)

        if revised_found:
            plan_file = revised
            state["plan_file"] = plan_file
            state["changed_since"] = changed_since
            print(f"  ✓ Revised plan found: {plan_file}")
        else:
            state["final_gate"] = "ESCALATE"
            state["escalate_reason"] = (
                f"Revision timeout at round {round_n}: convener {convener} did not produce "
                f"revised plan at {revised} within {REVISION_POLL_TIMEOUT}s."
            )
            state_file.write_text(json.dumps(state, indent=2))
            print(f"\n🚨 REVISION TIMEOUT — ESCALATE TO PETER")
            print(f"  Convener {convener} did not produce revised plan at {revised}.")
            print(f"  State: {state_file}")
            return "ESCALATE"

        active_reviewers = fails  # Only re-dispatch FAILers


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="GCG Council Consensus Loop — self-driving, loops until full consensus"
    )
    parser.add_argument("--plan", required=True, help="Plan slug (e.g. fleet-upgrade-2026-05)")
    parser.add_argument("--plan-file", required=True, help="Path to plan markdown file")
    parser.add_argument("--goal", required=True, help="Confirmed goal (one sentence)")
    parser.add_argument("--key-finding", default="", help="Most important research insight")
    parser.add_argument("--pattern", default="", help="Relevant prior council pattern")
    parser.add_argument(
        "--convener", default="daen",
        help="Convener agent who revises plan on FAIL (default: daen)"
    )
    args = parser.parse_args()

    result = run_loop(
        args.plan, args.plan_file, args.goal,
        args.key_finding, args.pattern, args.convener
    )
    sys.exit(0 if result in ("PASS", "CONDITIONAL") else 1)
