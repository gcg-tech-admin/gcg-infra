#!/usr/bin/env python3
"""
council_tick.py — Stateless council tick driver.

One tick = load manifest → atomic plan-hash verification → advance state.

Cron every 15 min per active manifest. Never blocks. No input().
Exit codes: 0=ok, 1=error

Usage:
    council_tick.py <slug>                  # tick a single council
    council_tick.py --all                   # tick all active councils
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
VERDICT_DIR = Path("/opt/gcg/shared/plans/reviews")
COUNCIL_LOOP_PATH = Path("/opt/gcg/shared/bin/council_loop.py")

REVIEWERS = ["SOCRATES", "NEMESIS", "CASSANDRA", "CONFUCIUS", "WONHOO", "VULCAN"]
REVIEWER_IDS = {
    "SOCRATES": "socrates",
    "NEMESIS": "nemesis",
    "CASSANDRA": "cassandra",
    "CONFUCIUS": "confucius",
    "WONHOO": "wonhoo",
    "VULCAN": "vulcan",
}
REVIEWER_ROLES = {
    "SOCRATES": "Question every assumption. Probe logic, expose contradictions, ask what the builder took for granted.",
    "NEMESIS": "Attack execution. Find what breaks under stress, what fails on agent 15 but not agent 1, what the rollback doesn't cover.",
    "CASSANDRA": "Project the future. What does this plan become in 3-6 months? Technical debt, scaling walls, lock-in, second-order effects.",
    "CONFUCIUS": "Verify every factual claim. Read the actual files. Check real configs. Confirm paths exist. Do NOT speculate — go look.",
    "WONHOO": "Practical feasibility. Will this actually work when a real person or agent executes it? Are steps clear? Timeline realistic? Simpler way?",
    "VULCAN": "QA verification: Read and verify the proposed changes against the live system. Confirm schema paths exist, test queries are valid, rollback steps are reversible. Reject any claim not verified against running infrastructure.",
}

REPOKE_MINUTES = 30
MAX_STUCK_ROUNDS = 3
MAX_REVISION_ROUNDS = 8
PLANNER_DEADLINE_HOURS = 2
ESCALATED_AUTO_ARCHIVE_HOURS = 24

# ── Verdict filename parsing (for historical-verdict globbing) ──────

def _parse_verdict_filename(filename: str, plan_slug: str) -> tuple[str, int]:
    """Parse reviewer ID and round number from a verdict filename.
    Returns (reviewer_id, round_number). Round 1 has no -R suffix.
    Examples: slug-socrates.md → (socrates, 1), slug-NEMESIS-R3.md → (nemesis, 3)
    """
    stem = Path(filename).stem.lower()
    suffix = stem[len(plan_slug.lower()) + 1:]  # +1 for separator hyphen
    # Try -R{N} or -r{N} round suffix first
    m = re.match(r'([a-z]+)-[rR](\d+)$', suffix)
    if m:
        reviewer = m.group(1)
        if reviewer in set(REVIEWER_IDS.values()):
            return reviewer, int(m.group(2))
        return "unknown", 1
    # Round 1: no round suffix, just the reviewer id
    if suffix in set(REVIEWER_IDS.values()):
        return suffix, 1
    return "unknown", 1


def extract_reviewer_from_filename(filename: str, plan_slug: str) -> str:
    """Extract reviewer ID from a verdict filename."""
    return _parse_verdict_filename(filename, plan_slug)[0]


def extract_round_from_filename(filename: str, plan_slug: str) -> int:
    """Extract round number from a verdict filename."""
    return _parse_verdict_filename(filename, plan_slug)[1]


# Guardian that relays terminal gates to Peter. There is NO `peter` inbox poller,
# so `fleet send peter` is a dead-end — route Peter-facing notices through the
# convener/guardian (daen), whose session IS wired to Peter's Telegram. Peter only
# ever sees a terminal gate (final plan on PASS, or a decision on SPLIT/STUCK).
GUARDIAN_AGENT = "daen"


def notify_peter(text: str):
    """Surface a terminal council gate to Peter via the guardian agent (daen).

    Routes to GUARDIAN_AGENT (which has an inbox poller + Telegram), NOT to the
    pollerless `peter` address. The prefix tells the guardian to relay to Peter.
    """
    subprocess.run(["fleet", "send", "--priority", "2", GUARDIAN_AGENT,
                    f"🔔 SURFACE TO PETER (council gate):\n\n{text}"], check=False)


# ── Pure helper functions (relocated from council_loop.py) ──────────────────

def hash_plan(plan_path: str) -> str:
    """SHA-256 hash of plan file. Double-read for atomicity."""
    path = Path(plan_path)
    if not path.exists():
        raise FileNotFoundError(f"Plan file not found: {plan_path}")

    read1 = path.read_bytes()
    time.sleep(0.1)  # small gap to catch mid-write
    read2 = path.read_bytes()

    if read1 != read2:
        raise IOError("Plan file changed during read (concurrent write detected)")

    return "sha256:" + hashlib.sha256(read1).hexdigest()


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


def extract_reviewed_hash(path: Path) -> str | None:
    """Extract reviewed-hash from verdict file footer."""
    try:
        text = path.read_text()
        m = re.search(r"reviewed-hash:\s*(sha256:[a-f0-9]+)", text)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def extract_required_changes(path: Path) -> str:
    """Extract 'Required changes' section from a FAIL verdict.

    Matches any heading level (###/##/# Required changes) or bare 'Required changes'
    (no leading #), case-insensitive. Falls back to full file body if section not
    found or empty.
    """
    try:
        text = path.read_text()
        m = re.search(
            r"(?:#{1,3}\s*)?Required\s*changes[^\n]*\n(.*?)(?=\n#{1,3}\s|\Z)",
            text, re.DOTALL | re.MULTILINE | re.IGNORECASE
        )
        if m and m.group(1).strip():
            return m.group(1).strip()[:800]
        # Section not found or empty → fallback to full body
        return text.strip()[:800]
    except Exception:
        pass
    return "(see verdict file)"


def verdict_file(plan_slug: str, reviewer: str, round_n: int) -> Path:
    """Resolve a reviewer's verdict file.

    CASE-FORK FIX (2026-06-22): reviewer agents save under inconsistent casing
    (NEMESIS-R4 / nemesis-R4 / Confucius-R5 / nemesis-r3). If we only looked for
    one casing, the tick missed the file, re-dispatched, and forked parallel
    verdict tracks that disagreed (one FAIL, one PASS). We now resolve ANY existing
    file case-insensitively (reviewer name + round suffix), returning the NEWEST on
    conflict; if none exists yet (dispatch), we return a canonical lowercase target
    so future writes converge to one track.
    """
    rid = REVIEWER_IDS.get(reviewer, reviewer).lower()
    canonical = (VERDICT_DIR / f"{plan_slug}-{rid}.md") if round_n == 1 \
        else (VERDICT_DIR / f"{plan_slug}-{rid}-R{round_n}.md")
    want = f"{plan_slug}-{rid}".lower() if round_n == 1 \
        else f"{plan_slug}-{rid}-r{round_n}".lower()
    matches = [p for p in VERDICT_DIR.glob("*.md") if p.stem.lower() == want]
    if matches:
        return max(matches, key=lambda p: p.stat().st_mtime)
    return canonical


# ── Dispatch helpers ────────────────────────────────────────────────────────

def dispatch_reviewer(plan_slug: str, plan_path: str, plan_hash: str, goal: str,
                      reviewer: str, round_n: int, key_finding: str = "",
                      pattern: str = "", prior_fail: str = ""):
    """Send a council review request to one reviewer via fleet send."""
    agent_id = REVIEWER_IDS[reviewer]
    role = REVIEWER_ROLES[reviewer]
    vf = verdict_file(plan_slug, reviewer, round_n)

    round_header = f"Round {round_n}"
    revision_ctx = ""
    if round_n > 1:
        revision_ctx = (
            f"\n\n⚠️ REVISION ROUND {round_n}: Plan has been revised since your last review."
            f"\nYour prior FAIL required:\n{prior_fail}"
            f"\nFocus ONLY on: (1) your previously flagged issues, (2) any sections touched by the revision."
            f"\n\nROOT CAUSE CHECK: Your required changes must target the ROOT CAUSE, not symptoms."
            f" If the revision addressed your root cause (even imperfectly), engage with what remains."
            f" If the revision did not touch your root cause at all, say so explicitly."
        )

    msg = (
        f"COUNCIL REVIEW REQUEST — {plan_slug} ({round_header})\n\n"
        f"Goal: {goal} | Plan: {plan_path}"
        + (f" | Key finding: {key_finding}" if key_finding else "")
        + (f" | Prior pattern: {pattern}" if pattern else "")
        + revision_ctx + "\n\n"
        f"Your role: {role}\n\n"
        f"MANDATORY SEQUENCE:\n"
        f"1. READ workspace/SOUL.md — your identity and review principles.\n"
        f"1b. APPLY THE PONYTAIL LENS (YAGNI / least-code, MANDATORY every review): hunt over-engineering — unnecessary mechanisms, speculative abstractions, 'might-need-later', anything reuse/delete beats building. Invoke the ponytail skill if installed.\n"
        f"2. READ workspace/memory/review-log.md — scan ALL past entries for patterns applicable to this plan. "
        f"You MUST list applicable patterns in your verdict. 'None applicable' is a valid answer but must be explicit.\n"
        f"3. REVIEW the plan against your role. Read the actual plan file at: {plan_path}\n"
        f"4. WRITE verdict to {vf} using EXACTLY this format:\n\n"
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
        f"5. EMBED to pgvector:\n"
        f"   /opt/gcg/shared/bin/memory capture --agent {agent_id} --memory-type lesson "
        f"--importance high --scope agent_private "
        f"\"Council review {plan_slug} R{round_n} {datetime.now(timezone.utc).strftime('%Y-%m-%d')}. "
        f"Verdict: [VERDICT]. Key findings: [summary]. New patterns: [any].\"\n"
        f"6. APPEND hash footer:\n"
        f"   reviewed-hash: {plan_hash}\n"
        f"7. CLOSE inbox: fleet done <message_id> && rm workspace/inbox/<message_id>.json"
    )

    # Bypass the Cascade-First guard: reviewer prompts legitimately contain "?" +
    # review verbs (e.g. Cassandra's "What does this become?"), which otherwise trips
    # the guard and rejects the dispatch (exit 7). Matches council_loop.py.
    subprocess.run(["fleet", "send", agent_id, msg], check=True,
                   env={**os.environ, "FLEET_SKIP_CASCADE_GUARD": "1"})
    print(f"  → {reviewer}: dispatched (round {round_n})")


def dispatch_planner_revision(planner_agent: str, plan_slug: str, plan_path: str,
                               conditions: list, round_n: int):
    """Wake the planner agent's MAIN session with the aggregated revision request.

    Uses `fleet wake` (→ POST /hooks/wake → system event into the agent's main
    session) NOT `fleet send` (→ inbox poll → isolated one-shot turn). The planner
    needs full session context to revise the plan, so it MUST land in main.
    Peter directive 2026-06-23: no headless claude -p, no inbox-poll revision."""
    conditions_text = "\n".join(f"- {c}" for c in conditions)
    msg = (
        f"COUNCIL REVISION REQUEST — {plan_slug} (Round {round_n} needs revision)\n\n"
        f"Plan: {plan_path}\n\n"
        f"The council completed Round {round_n} but did NOT reach full consensus.\n"
        f"Conditions and findings from reviewers:\n{conditions_text}\n\n"
        f"Please revise the plan to address each condition/finding at the ROOT CAUSE level.\n"
        f"Save the revised plan (same path or versioned), then the next tick will detect the hash change\n"
        f"and open a new round."
    )
    # Bypass the Cascade-First guard (same as dispatch_reviewer) — the revision request
    # legitimately contains review verbs that otherwise trip the guard (exit 7) and
    # silently kill the loop at the revise step. Hardened 2026-06-18.
    subprocess.run(["fleet", "wake", planner_agent, msg], check=True,
                   env={**os.environ, "FLEET_SKIP_CASCADE_GUARD": "1"})
    print(f"  → {planner_agent}: revision WAKE → main session (round {round_n})")


# ── Gate logic ──────────────────────────────────────────────────────────────

def compute_gate(verdicts: dict[str, str]) -> str:
    """
    Gate policy (Peter-ratified 2026-06-07):
    Only all-5-PASS ships.
    CONDITIONAL does NOT ship — triggers revision.
    SPLIT (any PASS + any FAIL) → escalate.
    """
    passes = [r for r, v in verdicts.items() if v == "PASS"]
    fails = [r for r, v in verdicts.items() if v in ("FAIL", "UNKNOWN")]
    conditionals = [r for r, v in verdicts.items() if v == "CONDITIONAL"]
    timeouts = [r for r, v in verdicts.items() if v == "TIMEOUT"]

    if not fails and not conditionals and not timeouts:
        return "PASS"
    if fails and not timeouts:
        # Any FAIL (even mixed with PASS) → FAIL → revise loop. Hardened 2026-06-18
        # (Peter directive: "loop until it finishes", don't dump to Peter): a fresh
        # PASS+FAIL mix is NOT a SPLIT-escalate — it revises. Genuine irreconcilable
        # splits still escalate via stuck-detection (same required changes 2 rounds)
        # in the FAIL/revise path. Supersedes the 2026-06-07 SPLIT-escalate policy.
        return "FAIL"
    if conditionals and not fails and not timeouts:
        # All present, only CONDITIONAL + PASS → fails the gate
        return "FAIL"
    # Timeout counts as fail but is handled separately
    return "FAIL"


# ── Manifest I/O ────────────────────────────────────────────────────────────

def load_manifest(slug: str) -> dict:
    """Load council manifest. Returns None if not found."""
    path = COUNCILS_DIR / f"{slug}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def save_manifest(slug: str, manifest: dict):
    """Atomic save (temp + rename). Tick is the ONLY writer."""
    path = COUNCILS_DIR / f"{slug}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=2))
    tmp.rename(path)


def archive_manifest(slug: str, prefix: str = ""):
    """Rename manifest to archive (removes from active rotation)."""
    path = COUNCILS_DIR / f"{slug}.json"
    if prefix:
        archived = COUNCILS_DIR / f"{prefix}-{slug}.json"
    else:
        archived = COUNCILS_DIR / f"archived-{slug}.json"
    path.rename(archived)
    print(f"  → Archived as: {archived.name}")


# ── Manifest-driving: council→cascade wiring (Task 1.4) ─────────────────────

CASCADE_DRIVER = Path("/opt/gcg/shared/bin/cascade-driver")


def _trigger_cascade(slug: str, cascade_manifest_path: str):
    """
    On council PASS: auto-register the approved plan as a cascade.
    Calls cascade-driver register <cascade_manifest_path> so the plan advances
    task-by-task through agent_messages with 0 manual fleet send calls.
    Idempotent — cascade-driver refuses to double-register the same plan_id.
    """
    mp = Path(cascade_manifest_path)
    if not mp.exists():
        print(f"  ⚠️  cascade_manifest_path not found: {mp} — skipping cascade registration")
        return
    if not CASCADE_DRIVER.exists():
        print(f"  ⚠️  cascade-driver not found at {CASCADE_DRIVER} — skipping cascade registration")
        return
    print(f"  → Triggering cascade: cascade-driver register {mp}")
    try:
        result = subprocess.run(
            [sys.executable, str(CASCADE_DRIVER), "register", str(mp)],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "FLEET_SKIP_CASCADE_GUARD": "1"},
        )
        for line in result.stdout.strip().splitlines():
            print(f"    {line}")
        if result.returncode != 0:
            print(f"  ⚠️  cascade-driver returned exit {result.returncode}: {result.stderr.strip()}")
        else:
            print(f"  → Cascade registered for council '{slug}'")
    except Exception as e:
        print(f"  ⚠️  cascade registration failed (non-fatal): {e}")


# ── Tick logic ──────────────────────────────────────────────────────────────

def tick(slug: str, goal: str = "", key_finding: str = "", pattern: str = ""):
    """
    Execute one tick for a council.
    Returns manifest status after tick.
    """
    manifest = load_manifest(slug)
    if manifest is None:
        print(f"❌ Council '{slug}' not found in {COUNCILS_DIR}")
        return "error"

    # ── Stale-clear: lock persisting across ticks = previous tick crashed after
    # lock-set. The lock is synchronous; any persisted lock is stale by definition.
    if manifest.get("revision_in_progress"):
        manifest["revision_in_progress"] = False
        save_manifest(slug, manifest)

    plan_path = manifest.get("plan_path", "")
    plan_hash = manifest.get("plan_hash", "")
    round_n = manifest.get("round", 1)
    status = manifest.get("status", "in_review")
    reviewers_expected = manifest.get("reviewers", list(REVIEWER_IDS.values()))
    stuck_count = manifest.get("stuck_count", 0)
    planner_deadline_h = manifest.get("planner_deadline_h", PLANNER_DEADLINE_HOURS)
    escalated_at = manifest.get("escalated_at")
    last_tick = manifest.get("last_tick")
    planner_agent = manifest.get("planner_agent", "daen")
    now = datetime.now(timezone.utc)

    # ── Update last_tick ──
    manifest["last_tick"] = now.isoformat()

    print(f"\n{'='*60}")
    print(f"TICK: {slug} | R{round_n} | status={status} | stuck={stuck_count}")
    print(f"{'='*60}")

    # ── 1. Atomic plan-hash verification ──
    try:
        current_hash = hash_plan(plan_path)
    except FileNotFoundError as e:
        print(f"❌ {e}")
        return "error"
    except IOError as e:
        print(f"⚠️  {e} — skipping tick, will retry next cycle")
        save_manifest(slug, manifest)
        return "retry"

    # ── 2. Check if plan changed (mid-round revision) ──
    if status == "in_review" and current_hash != plan_hash:
        # Plan changed mid-round — invalidate round, bump, re-dispatch
        plan_hash = current_hash
        round_n += 1
        manifest["plan_hash"] = plan_hash
        manifest["round"] = round_n
        manifest["stuck_count"] = 0  # revision = progress
        manifest["status"] = "in_review"
        # Reset verdicts for this round
        for r in reviewers_expected:
            vf = verdict_file(slug, r.upper(), round_n)
            if vf.exists():
                vf.unlink()

        print(f"  Plan hash changed — invalidating round, opening R{round_n}")
        for r_name in reviewers_expected:
            dispatch_reviewer(slug, plan_path, plan_hash, goal,
                              r_name.upper(), round_n, key_finding, pattern)

        manifest["last_dispatch"] = now.isoformat()
        save_manifest(slug, manifest)
        return "revised"

    # ── 3. Handle 'revising' status ──
    if status == "revising":
        # 3a. Planner produced a revision (plan hash changed) → open the next round.
        # Without this, a revised plan sits undetected until the deadline and the
        # loop wrongly escalates — the revision→re-review transition never fires.
        if current_hash != plan_hash:
            plan_hash = current_hash
            round_n += 1
            manifest["plan_hash"] = plan_hash
            manifest["round"] = round_n
            manifest["stuck_count"] = stuck_count  # carried; reset only on real progress below
            manifest["status"] = "in_review"
            for r in reviewers_expected:
                vf = verdict_file(slug, r.upper(), round_n)
                if vf.exists():
                    vf.unlink()
            print(f"  Planner revised plan (hash changed) — opening R{round_n}, re-dispatching reviewers")
            for r_name in reviewers_expected:
                dispatch_reviewer(slug, plan_path, plan_hash, goal,
                                  r_name.upper(), round_n, key_finding, pattern)
            manifest["last_dispatch"] = now.isoformat()
            save_manifest(slug, manifest)
            return "revised"

        # 3b. No revision yet — escalate if planner blew the deadline.
        # Measure from when revision was REQUESTED (revising_since), not last_tick
        # (which updates every tick and would make the deadline unreachable).
        revising_since = manifest.get("revising_since") or last_tick
        since_dt = datetime.fromisoformat(revising_since) if revising_since else now
        elapsed_h = (now - since_dt).total_seconds() / 3600
        if elapsed_h > planner_deadline_h:
            print(f"  Planner ({planner_agent}) unresponsive for {elapsed_h:.1f}h — escalating to Peter")
            manifest["status"] = "escalated"
            manifest["escalated_at"] = now.isoformat()
            # 🔒 IDEMPOTENT NOTIFY: only send ONCE per council.
            # `escalation_notified` survives cross-manifest resets (ponytail, superseded).
            if not manifest.get("escalation_notified"):
                notify_peter(
                    f"🚨 COUNCIL ESCALATION: {slug} — Planner {planner_agent} "
                    f"unresponsive for {elapsed_h:.1f}h (deadline {planner_deadline_h}h). "
                    f"Council stuck in 'revising' since round {round_n}.")
                manifest["escalation_notified"] = True
            else:
                print(f"  (notification already sent for this council — suppressed)")
            save_manifest(slug, manifest)
            return "escalated"

        print(f"  Revising (planner deadline {planner_deadline_h}h, elapsed {elapsed_h:.1f}h)")
        save_manifest(slug, manifest)
        return "revising"

    # ── 4. Handle superseded status — archive immediately, no more ticks ──
    if status == "superseded":
        print(f"  Council superseded — archiving to stop ticks")
        archive_manifest(slug, "superseded-archived")
        return "archived"

    # ── 5. Handle escalated status — auto-archive after 24h ──
    if status == "escalated" and escalated_at:
        escalated_dt = datetime.fromisoformat(escalated_at)
        elapsed_h = (now - escalated_dt).total_seconds() / 3600
        if elapsed_h > ESCALATED_AUTO_ARCHIVE_HOURS:
            print(f"  Escalated for {elapsed_h:.1f}h (> {ESCALATED_AUTO_ARCHIVE_HOURS}h) — auto-archiving as [unresolved]")
            archive_manifest(slug, "unresolved")
            return "archived"

        print(f"  Escalated ({elapsed_h:.1f}h elapsed)")
        save_manifest(slug, manifest)
        return "escalated"

    # ── 5. Handle 'passed' status — already done ──
    if status in ("passed", "archived"):
        print(f"  Already {status} — no action")
        return status

    # ── 6. Main: scan verdicts for current round ──
    verdicts: dict[str, str] = {}
    stale_verdicts = 0
    missing_reviewers = []

    for r_name in reviewers_expected:
        r_upper = r_name.upper()
        vf = verdict_file(slug, r_upper, round_n)

        if not vf.exists():
            missing_reviewers.append(r_name)
            continue

        # Check hash binding — ignore verdicts with stale hash
        reviewed_hash = extract_reviewed_hash(vf)
        if reviewed_hash and reviewed_hash != plan_hash:
            print(f"  ⚠️  {r_upper}: verdict has stale hash ({reviewed_hash[:20]}...) — ignoring")
            stale_verdicts += 1
            continue

        verdict = parse_verdict(vf)
        verdicts[r_name] = verdict
        print(f"  ✓ {r_upper}: {verdict}")

    # ── 6a. Handle stale/stale-hash edge case ──
    # Reviewers with stale-hash verdicts need re-dispatch too
    stale_reviewers = []
    if stale_verdicts > 0:
        for r_name in reviewers_expected:
            r_upper = r_name.upper()
            vf = verdict_file(slug, r_upper, round_n)
            if vf.exists():
                rh = extract_reviewed_hash(vf)
                if rh and rh != plan_hash:
                    stale_reviewers.append(r_name)
    
    # ── 6b. Re-poke stragglers (interval-gated: never more than once per REPOKE_MINUTES) ──
    repoke_targets = list(set(missing_reviewers + stale_reviewers))
    if repoke_targets:
        last_dispatch = manifest.get("last_dispatch")
        mins_since = ((now - datetime.fromisoformat(last_dispatch)).total_seconds() / 60
                      if last_dispatch else REPOKE_MINUTES + 1)
        if mins_since < REPOKE_MINUTES:
            print(f"  Missing verdicts: {repoke_targets} — waiting "
                  f"({mins_since:.0f}/{REPOKE_MINUTES}min since last dispatch, no re-poke yet)")
            save_manifest(slug, manifest)
            return "waiting"
        print(f"  Missing/stale verdicts: {repoke_targets}")
        print(f"  Re-poking ({mins_since:.0f}min since last dispatch ≥ {REPOKE_MINUTES}min)...")
        for r_name in repoke_targets:
            dispatch_reviewer(slug, plan_path, plan_hash, goal,
                              r_name.upper(), round_n, key_finding, pattern,
                              prior_fail=extract_required_changes(
                                  verdict_file(slug, r_name.upper(), round_n - 1)
                              ) if round_n > 1 else "")
        manifest["last_dispatch"] = now.isoformat()
        save_manifest(slug, manifest)
        return "repoked"

    # ── 7. Compute gate ──
    gate = compute_gate(verdicts)
    print(f"  Gate: {gate}")

    if gate == "PASS":
        # All 5 PASS — success
        manifest["status"] = "passed"
        manifest["history"].append({"round": round_n, "gate": "PASS"})
        save_manifest(slug, manifest)
        print(f"\n✅ ALL-5-PASS — {slug} complete")
        # Notify Peter
        notify_peter(
            f"✅ COUNCIL PASS: {slug} passed all-5-PASS (round {round_n}).\n"
            f"Final plan: {plan_path}\n"
            f"This is a solution + plan ready for your go/no-go.")
        # Persist learnings
        try:
            subprocess.run([sys.executable,
                           str(COUNCIL_LOOP_PATH.parent / "council-persist.py"),
                           "--plan", slug], check=False)
            print("  → council-persist.py called")
        except Exception as e:
            print(f"  ⚠️  council-persist.py failed: {e}")
        # Auto-register cascade if manifest specifies one (manifest-driving — Task 1.4)
        cascade_manifest_path = manifest.get("cascade_manifest_path")
        if cascade_manifest_path:
            _trigger_cascade(slug, cascade_manifest_path)
        # Archive
        archive_manifest(slug)
        return "passed"

    elif gate == "SPLIT":
        # Some PASS + some FAIL — escalate to Peter
        print(f"\n🚨 SPLIT — escalating to Peter (PASS: {verdicts.keys()})")
        manifest["status"] = "escalated"
        manifest["escalated_at"] = now.isoformat()
        manifest["history"].append({"round": round_n, "gate": "SPLIT"})
        save_manifest(slug, manifest)
        notify_peter(
            f"🚨 COUNCIL SPLIT: {slug} round {round_n} — "
            f"reviewers disagree. Verdicts: {verdicts}. "
            f"Peter decision required.")
        return "split"

    elif gate == "FAIL":
        # Any CONDITIONAL or FAIL — cycle to revision
        stuck_count += 1
        manifest["stuck_count"] = stuck_count

        # Historical globbing: gather conditions from ALL prior rounds too.
        # Match both R1 (slug-reviewer.md, no -R suffix) and R2+ (slug-reviewer-RN.md).
        all_findings = []
        for vf_path in sorted(VERDICT_DIR.glob(f'{slug}-*.md')):
            reviewer, vf_round = _parse_verdict_filename(vf_path.name, slug)
            if reviewer == 'unknown':
                continue
            if vf_round < round_n:
                v = parse_verdict(vf_path)
                if v in ('FAIL', 'CONDITIONAL'):
                    rc = extract_required_changes(vf_path)
                    all_findings.append(f'[{reviewer}] (R{vf_round} {v}): {rc}')

        # Current-round conditions
        fail_notes = {}
        for r_name, v in verdicts.items():
            vf = verdict_file(slug, r_name.upper(), round_n)
            rc = extract_required_changes(vf)
            fail_notes[r_name] = rc
            all_findings.append(f"[{r_name}] ({v}): {rc}")

        conditions = all_findings

        if stuck_count >= MAX_STUCK_ROUNDS:
            # 3 stuck → escalate Peter
            print(f"\n🚨 STUCK ({stuck_count}/{MAX_STUCK_ROUNDS}) — escalating to Peter")
            manifest["status"] = "escalated"
            manifest["escalated_at"] = now.isoformat()
            manifest["history"].append({
                "round": round_n, "gate": "STUCK",
                "fails": [r for r, v in verdicts.items() if v != "PASS"]
            })
            save_manifest(slug, manifest)
            notify_peter(
                f"🚨 COUNCIL STUCK: {slug} — {stuck_count} consecutive rounds without PASS. "
                f"Last round: {verdicts}. "
                f"Conditions from reviewers:\n" + "\n".join(conditions))
            return "stuck_escalated"

        # Normal revision cycle
        print(f"\n🔄 Revision needed (stuck_count={stuck_count})")

        # Revision lock: prevent concurrent planner-revision wakes
        if manifest.get("revision_in_progress"):
            print(f"  Revision already in progress — skipping tick")
            return "revising"

        manifest["revision_in_progress"] = True
        manifest["revision_lock_since"] = now.isoformat()
        save_manifest(slug, manifest)

        # Wake the planner's MAIN session with the aggregated 5-reviewer conditions.
        # No headless claude -p, no inbox poll — Peter directive 2026-06-23.
        # The planner revises in-session and saves; next tick detects the hash
        # change and opens a new round.
        manifest["status"] = "revising"
        manifest["revising_since"] = now.isoformat()
        manifest["history"].append({
            "round": round_n, "gate": "FAIL",
            "fails": [r for r, v in verdicts.items() if v != "PASS"],
            "revision_type": "wake"
        })
        save_manifest(slug, manifest)
        dispatch_planner_revision(planner_agent, slug, plan_path, conditions, round_n)
        manifest["revision_in_progress"] = False
        save_manifest(slug, manifest)
        return "revision_dispatched"

    else:
        print(f"  Unknown gate: {gate}")
        save_manifest(slug, manifest)
        return "unknown"


def tick_all():
    """Tick all active councils (non-archived, non-passed manifests)."""
    print(f"\n{'='*60}")
    print(f"COUNCIL TICK — ALL ({datetime.now(timezone.utc).isoformat()})")
    print(f"{'='*60}")

    if not COUNCILS_DIR.exists():
        print(f"  No councils dir at {COUNCILS_DIR}")
        return

    manifests = sorted(COUNCILS_DIR.glob("*.json"))
    active = [m for m in manifests
              if not m.name.startswith("archived-")
              and not m.name.startswith("unresolved-")]

    if not active:
        print("  No active councils")
        return

    for mf in active:
        slug = mf.stem
        manifest = load_manifest(slug)
        if manifest and manifest.get("status") in ("passed", "archived"):
            continue
        try:
            tick(slug)
        except Exception as e:
            print(f"  ❌ Tick failed for {slug}: {e}")

    print(f"\n{'='*60}")
    print(f"TICK ALL — done")
    print(f"{'='*60}")


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Stateless council tick driver")
    parser.add_argument("slug", nargs="?", help="Council slug to tick")
    parser.add_argument("--all", action="store_true", help="Tick all active councils")
    parser.add_argument("--goal", default="", help="Council goal (for dispatch messages)")
    parser.add_argument("--key-finding", default="", help="Key finding context")
    parser.add_argument("--pattern", default="", help="Relevant council pattern")
    args = parser.parse_args()

    COUNCILS_DIR.mkdir(parents=True, exist_ok=True)
    VERDICT_DIR.mkdir(parents=True, exist_ok=True)

    if args.all:
        tick_all()
    elif args.slug:
        tick(args.slug, args.goal, args.key_finding, args.pattern)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
