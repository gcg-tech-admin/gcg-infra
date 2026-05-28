#!/usr/bin/env python3
"""
council-persist.py — Deterministic post-council persistence script.
Extracts verdicts from council review files and appends to reviewer review-logs.
No LLM involved. Pure text parsing.

Usage:
  # After a council session — process all verdict files for a plan:
  council-persist.py --plan lightrag-sprint-v1

  # Backfill all historical verdict files:
  council-persist.py --backfill

  # Process a specific verdict file:
  council-persist.py --file /path/to/plan-NEMESIS.md --reviewer nemesis --plan-name "My Plan" --date 2026-03-25
"""

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PLANS_DIR = Path("/opt/gcg/shared/plans/reviews")
AGENTS_DIR = Path("/opt/gcg/openclaw-daen/workspace/agents")  # legacy fallback
COUNCIL_AGENTS_DIR = Path("/opt/gcg")  # standalone agents: /opt/gcg/openclaw-<name>/workspace/memory/

REVIEWERS = {
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

def get_review_log_path(reviewer: str) -> Path:
    # Standalone agent path (2026-05-27): /opt/gcg/openclaw-<name>/workspace/memory/review-log.md
    standalone = COUNCIL_AGENTS_DIR / f"openclaw-{reviewer}" / "workspace" / "memory" / "review-log.md"
    if standalone.parent.exists():
        return standalone
    # Fallback to legacy daen/workspace/agents/<name>/ path
    return AGENTS_DIR / reviewer / "memory" / "review-log.md"

def extract_verdict(content: str) -> str:
    """Extract PASS/FAIL/CONDITIONAL from verdict file content."""
    # Try structured format first
    for pattern in [
        r'\*\*Verdict[:\s]*\*\*\s*(PASS|FAIL|CONDITIONAL|REVISE)',
        r'Verdict[:\s]*(PASS|FAIL|CONDITIONAL|REVISE)',
        r'^(PASS|FAIL|CONDITIONAL|REVISE)\b',
        r'## Verdict\s*\n\s*(PASS|FAIL|CONDITIONAL|REVISE)',
        r'VERDICT[:\s]*(PASS|FAIL|CONDITIONAL|REVISE)',
    ]:
        m = re.search(pattern, content, re.MULTILINE | re.IGNORECASE)
        if m:
            return m.group(1).upper()

    # Check first line for bare verdict
    first_line = content.strip().split('\n')[0].strip()
    for v in ['PASS', 'FAIL', 'CONDITIONAL', 'REVISE']:
        if first_line.upper().startswith(v):
            return v

    return "UNKNOWN"

def extract_findings(content: str, max_findings: int = 5) -> list[str]:
    """Extract key findings — look for numbered items, bullet points after headings."""
    findings = []

    # Look for critical/high findings sections
    sections = re.split(r'\n##\s+', content)
    for section in sections:
        lower = section.lower()
        if any(kw in lower for kw in ['critical', 'high', 'failure', 'attack', 'project', 'flagged', 'finding', 'issue', 'what fail', 'what break']):
            # Extract bullet points or numbered items
            for line in section.split('\n'):
                line = line.strip()
                if line and (line.startswith('-') or line.startswith('*') or re.match(r'^\d+\.', line)):
                    # Clean up markdown
                    cleaned = re.sub(r'^\s*[-*]\s*', '', line)
                    cleaned = re.sub(r'^\d+\.\s*', '', cleaned)
                    cleaned = re.sub(r'\*\*([^*]+)\*\*', r'\1', cleaned)  # Remove bold
                    if len(cleaned) > 20:  # Skip trivial lines
                        findings.append(cleaned[:200])  # Cap length
                        if len(findings) >= max_findings:
                            return findings

    # Fallback: look for any strong assertions
    if not findings:
        for line in content.split('\n'):
            line = line.strip()
            if any(kw in line.lower() for kw in ['race condition', 'will fail', 'breaks', 'undefined', 'missing', 'incorrect', 'wrong', 'does not exist', 'no evidence']):
                cleaned = re.sub(r'\*\*([^*]+)\*\*', r'\1', line)
                cleaned = re.sub(r'^\s*[-*]\s*', '', cleaned)
                if len(cleaned) > 20:
                    findings.append(cleaned[:200])
                    if len(findings) >= max_findings:
                        return findings

    return findings

def extract_watch_patterns(content: str, reviewer: str) -> list[str]:
    """Extract generalizable patterns worth watching for next time."""
    patterns = []

    # Look for recommendation/watch/pattern sections
    for line in content.split('\n'):
        line = line.strip()
        lower = line.lower()
        if any(kw in lower for kw in ['watch:', 'pattern:', 'recurring', 'always check', 'always verify', 'common in', 'consistently']):
            cleaned = re.sub(r'^\s*[-*]\s*', '', line)
            cleaned = re.sub(r'\*\*([^*]+)\*\*', r'\1', cleaned)
            if len(cleaned) > 15:
                patterns.append(cleaned[:200])

    return patterns[:3]

def format_log_entry(date: str, plan_name: str, verdict: str, findings: list[str], patterns: list[str], reviewer: str) -> str:
    """Format a review-log entry based on reviewer type."""
    lines = [f"\n## {date} — {plan_name}"]

    # Reviewer-specific prefix for findings
    prefixes = {
        "socrates": "Flagged",
        "nemesis": "Attacked",
        "cassandra": "Projected",
        "confucius": "Verified",
        "wonhoo": "Flagged",
    }
    prefix = prefixes.get(reviewer, "Found")

    for f in findings:
        lines.append(f"- {prefix}: {f} → {verdict}")

    if not findings:
        lines.append(f"- Verdict: {verdict} (findings not parseable from file — read original verdict)")

    for p in patterns:
        lines.append(f"- Watch: {p}")

    return "\n".join(lines) + "\n"

def entry_exists(log_path: Path, date: str, plan_name: str) -> bool:
    """Check if this entry already exists in the review-log."""
    if not log_path.exists():
        return False
    content = log_path.read_text()
    # Check for exact date+plan combo
    return f"## {date} — {plan_name}" in content

def append_to_log(reviewer: str, entry: str):
    """Append entry to reviewer's review-log.md."""
    log_path = get_review_log_path(reviewer)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    if not log_path.exists():
        # Create with header
        header = f"# {reviewer.capitalize()} — Review Log\n\nPersonal pattern library. Read this before every review.\n\n---\n"
        log_path.write_text(header + entry)
    else:
        with open(log_path, 'a') as f:
            f.write(entry)

    print(f"  → Appended to {log_path}")

def parse_verdict_file(filepath: Path, reviewer: str, plan_name: str, date: str):
    """Parse a single verdict file and append to reviewer's review-log."""
    content = filepath.read_text()

    if len(content.strip()) < 10:
        print(f"  Skipping {filepath.name} — too short ({len(content)} bytes)")
        return

    verdict = extract_verdict(content)
    findings = extract_findings(content)
    patterns = extract_watch_patterns(content, reviewer)

    log_path = get_review_log_path(reviewer)
    if entry_exists(log_path, date, plan_name):
        print(f"  Skipping {filepath.name} — entry already exists for {date}/{plan_name}")
        return

    entry = format_log_entry(date, plan_name, verdict, findings, patterns, reviewer)
    append_to_log(reviewer, entry)
    print(f"  {reviewer}: {verdict} | {len(findings)} findings | {len(patterns)} patterns")

    # Embed findings to pgvector so future sessions can recall them
    embed_to_pgvector(reviewer, date, plan_name, verdict, findings, patterns)

def embed_to_pgvector(reviewer: str, date: str, plan_name: str, verdict: str, findings: list, patterns: list):
    """Embed council review findings to pgvector for future session recall."""
    findings_text = " | ".join(findings[:5]) if findings else "no structured findings"
    patterns_text = " | ".join(patterns[:3]) if patterns else "no new patterns"
    text = (
        f"Council review {plan_name} {date}. Reviewer: {reviewer}. Verdict: {verdict}. "
        f"Key findings: {findings_text}. Watch patterns: {patterns_text}."
    )
    try:
        result = subprocess.run(
            ["/opt/gcg/shared/bin/memory", "capture",
             "--agent", reviewer,
             "--memory-type", "lesson",
             "--importance", "high",
             "--scope", "agent_private",
             text],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            print(f"  → pgvector embed OK ({reviewer})")
        else:
            print(f"  → pgvector embed FAILED ({reviewer}): {result.stderr.strip()[:200]}")
    except Exception as e:
        print(f"  → pgvector embed ERROR ({reviewer}): {e}")

def detect_reviewer_from_filename(filename: str) -> str | None:
    """Extract reviewer name from verdict filename."""
    for key, val in REVIEWERS.items():
        if key in filename:
            return val
    return None

def detect_date_from_filename(filename: str) -> str:
    """Extract or guess date from filename."""
    # Try YYYY-MM-DD pattern
    m = re.search(r'(\d{4}-\d{2}-\d{2})', filename)
    if m:
        return m.group(1)
    return "unknown-date"

def detect_plan_name_from_filename(filename: str) -> str:
    """Extract plan name from filename, stripping reviewer suffix."""
    name = filename.replace('.md', '')
    for key in REVIEWERS:
        name = name.replace(f'-{key}', '').replace(f'_{key}', '')
    # Clean up trailing version numbers for display
    return name

def backfill():
    """Process all historical verdict files."""
    print("=== BACKFILL MODE — Processing all historical verdict files ===\n")

    # Find all verdict files (files with reviewer names in them)
    verdict_files = []
    for f in sorted(PLANS_DIR.iterdir()):
        if not f.is_file() or not f.name.endswith('.md'):
            continue
        reviewer = detect_reviewer_from_filename(f.name)
        if reviewer:
            verdict_files.append((f, reviewer))

    print(f"Found {len(verdict_files)} verdict files\n")

    # Group by plan for cleaner output
    processed = 0
    for filepath, reviewer in verdict_files:
        plan_name = detect_plan_name_from_filename(filepath.name)
        date = detect_date_from_filename(filepath.name)

        # For files without dates, try to get from file mtime
        if date == "unknown-date":
            mtime = datetime.fromtimestamp(filepath.stat().st_mtime)
            date = mtime.strftime("%Y-%m-%d")

        print(f"Processing: {filepath.name}")
        parse_verdict_file(filepath, reviewer, plan_name, date)
        processed += 1

    print(f"\n=== Done. Processed {processed} verdict files. ===")

def process_plan(plan_slug: str):
    """Process all verdict files for a specific plan."""
    print(f"=== Processing verdict files for plan: {plan_slug} ===\n")

    today = datetime.now().strftime("%Y-%m-%d")
    processed = 0

    for f in sorted(PLANS_DIR.iterdir()):
        if not f.is_file() or not f.name.endswith('.md'):
            continue
        if plan_slug not in f.name:
            continue
        reviewer = detect_reviewer_from_filename(f.name)
        if not reviewer:
            continue

        date = detect_date_from_filename(f.name)
        if date == "unknown-date":
            date = today

        plan_name = detect_plan_name_from_filename(f.name)
        print(f"Processing: {f.name}")
        parse_verdict_file(f, reviewer, plan_name, date)
        processed += 1

    if processed == 0:
        print(f"No verdict files found matching '{plan_slug}'")
    else:
        print(f"\n=== Done. Processed {processed} files. ===")

def main():
    parser = argparse.ArgumentParser(description="Council verdict persistence")
    parser.add_argument("--backfill", action="store_true", help="Process all historical verdict files")
    parser.add_argument("--plan", type=str, help="Process verdict files for a specific plan slug")
    parser.add_argument("--file", type=str, help="Process a single verdict file")
    parser.add_argument("--reviewer", type=str, help="Reviewer name (with --file)")
    parser.add_argument("--plan-name", type=str, help="Plan name (with --file)")
    parser.add_argument("--date", type=str, help="Date (with --file)")
    parser.add_argument("--plans-dir", type=str, help="Override plans directory")
    parser.add_argument("--agents-dir", type=str, help="Override agents directory")

    args = parser.parse_args()

    global PLANS_DIR, AGENTS_DIR
    if args.plans_dir:
        PLANS_DIR = Path(args.plans_dir)
    if args.agents_dir:
        AGENTS_DIR = Path(args.agents_dir)

    if args.backfill:
        backfill()
    elif args.plan:
        process_plan(args.plan)
    elif args.file:
        if not args.reviewer:
            print("ERROR: --reviewer required with --file")
            sys.exit(1)
        filepath = Path(args.file)
        plan_name = args.plan_name or detect_plan_name_from_filename(filepath.name)
        date = args.date or detect_date_from_filename(filepath.name)
        parse_verdict_file(filepath, args.reviewer, plan_name, date)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
