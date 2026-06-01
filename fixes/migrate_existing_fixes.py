#!/usr/bin/env python3
"""
Phase 3: Migrate 14 existing fix docs to registry format.

Reads docs from /opt/gcg/shared/docs/research/claude-global-memory/*fix*.md
and writes registry entries to /opt/gcg/infra-repo/fixes/ as YAML-frontmatter markdown.

Usage:
    python3 migrate_existing_fixes.py        # dry-run by default
    python3 migrate_existing_fixes.py --exec  # actually write files

Output:
    Registers entries as fixes/fix-YYYY-MM-DD-slug.md
    Generates fixes/migration-report.md on completion
"""

import os
import re
import sys
import json
import yaml
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────
SRC_DIR = Path("/opt/gcg/shared/docs/research/claude-global-memory")
DST_DIR = Path("/opt/gcg/infra-repo/fixes")
ARCHIVE_DIR = Path("/opt/gcg/shared/docs/research/claude-global-memory/.archived")

# ── Mapping ────────────────────────────────────────────────────────────────
AGENT_MAP = {
    "mnemosyne": "mnemosyne",
    "daen": "daen",
    "talos": "talos",
    "vulcan": "vulcan",
    "argus": "argus",
}

SEVERITY_MAP = {
    "crit": "crit",
    "critical": "crit",
    "warn": "warn",
    "warning": "warn",
    "info": "info",
    "low": "info",
    "minor": "info",
    "enhancement": "info",
}

SYSTEM_KEYWORDS = {
    "embedding": "embedding-pipeline",
    "agent-fleet": "agent-fleet",
    "postgresql": "postgresql",
    "docker": "docker",
    "backup": "backup",
    "cron": "cron-scheduler",
    "api": "api-gateway",
    "security": "security",
    "messaging": "messaging",
    "infrastructure": "infrastructure",
}


def parse_frontmatter(text: str) -> dict:
    """Parse YAML frontmatter from markdown."""
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return {}
    try:
        return yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}


def strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter, return body text."""
    return re.sub(r"^---\s*\n.*?\n---\n*", "", text, count=1, flags=re.DOTALL)


def extract_body_sections(body: str) -> dict:
    """Extract structured sections from the body text using heuristics."""
    sections = {
        "symptom": "",
        "root_cause": "",
        "solution": "",
        "files_changed": [],
        "lessons_learned": "",
        "prevention": "",
        "detection_method": "",
        "resolution_time_minutes": None,
    }

    # Extract sections marked with **bold headers** or standalone lines
    # Root cause patterns
    rc_match = re.search(
        r"(?:Root cause|Root Cause).*?[:：]\s*(.+?)(?:\n\n|\n(?:\*\*|Why|Solution|How to))",
        body, re.DOTALL | re.IGNORECASE
    )
    if rc_match:
        sections["root_cause"] = rc_match.group(1).strip()

    # Solution / Fixed patterns
    sol_match = re.search(
        r"(?:Solution|Fixed|Fix(?:ed)?)\s*[:：]?\s*(.+?)(?:\n\n|\n(?:\*\*|Why|Root|Status|Result))",
        body, re.DOTALL | re.IGNORECASE
    )
    if sol_match:
        sections["solution"] = sol_match.group(1).strip()

    # Why sections (detection context)
    why_match = re.search(
        r"\*\*Why:\*\*\s*(.+?)(?:\n\n|\n(?:\*\*|How))",
        body, re.DOTALL | re.IGNORECASE
    )
    if why_match:
        sections["detection_method"] = why_match.group(1).strip()

    # How to apply (prevention)
    apply_match = re.search(
        r"\*\*How to apply:\*\*\s*(.+?)(?:\n\n|\n(?:\*\*|$))",
        body, re.DOTALL | re.IGNORECASE
    )
    if apply_match:
        sections["prevention"] = apply_match.group(1).strip()

    # Files changed: look for file paths in code blocks or inline
    file_patterns = re.findall(
        r"`(/opt/gcg/[^`]+)`|(/opt/gcg/[^\s\)\]]+)",
        body
    )
    seen = set()
    for match in file_patterns:
        path = match[0] or match[1]
        if path not in seen and any(kw in path for kw in ["gcg", "openclaw", "shared"]):
            seen.add(path)
            sections["files_changed"].append({
                "path": path,
                "action": "modified",
            })

    # Resolution time: look for patterns like "5 days", "2 hours", etc.
    time_match = re.search(
        r"(\d+)\s*(hour|minute|day)s?\s+for\b|resolved in\s+(\d+)\s*(hour|minute)",
        body, re.IGNORECASE
    )
    if time_match:
        val = int(time_match.group(1) or time_match.group(3))
        unit = (time_match.group(2) or time_match.group(4)).lower()
        if unit.startswith("hour"):
            sections["resolution_time_minutes"] = val * 60
        elif unit.startswith("minute"):
            sections["resolution_time_minutes"] = val
        elif unit.startswith("day"):
            sections["resolution_time_minutes"] = val * 1440

    # First paragraph after frontmatter = symptom
    body_clean = body.strip()
    first_para = body_clean.split("\n\n")[0] if body_clean else ""
    first_para = re.sub(r"^\*\*.*?\*\*\s*", "", first_para).strip()
    first_para = re.sub(r"^Why:?\s*", "", first_para).strip()
    if first_para and len(first_para) > 20 and len(first_para) < 1000:
        sections["symptom"] = first_para

    return sections


def determine_severity(fm: dict, body: str) -> str:
    """Map doc to a severity level based on content."""
    desc = (fm.get("description") or "").lower()
    name = (fm.get("name") or "").lower()
    combined = desc + " " + name + " " + body.lower()

    if any(kw in combined for kw in [
        "critical", "crit", "99 error", "crash", "outage", "data loss",
        "production", "blocking", "99%", "disk"
    ]):
        return "crit"
    if any(kw in combined for kw in [
        "warn", "degraded", "broken", "stuck", "stale", "partial",
        "bug", "error", "fix", "false"
    ]):
        return "warn"
    return "info"


def determine_systems(fm: dict) -> list:
    """Determine affected systems from tags."""
    tags = fm.get("tags", [])
    systems = set()
    for tag in tags:
        for kw, sys_name in SYSTEM_KEYWORDS.items():
            if kw in tag.lower():
                systems.add(sys_name)
    if not systems:
        systems.add("infrastructure")
    return sorted(systems)


def slugify(title: str) -> str:
    """Create a URL-safe slug from title."""
    slug = title.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug


def build_fix_entry(fm: dict, body: str, filename: str) -> dict:
    """Build a registry entry dict from a source doc."""
    # Parse date: prefer filename date (actual fix date), fallback to effective field
    date_match = re.search(r"(\d{4})[_-](\d{2})[_-](\d{2})", filename)
    if date_match:
        y, m, d = date_match.groups()
        dt = datetime(int(y), int(m), int(d), tzinfo=timezone.utc)
    else:
        date_str = fm.get("effective") or ""
        if date_str:
            try:
                dt = datetime.fromisoformat(date_str)
            except (ValueError, TypeError):
                dt = datetime.now(timezone.utc)
        else:
            dt = datetime.now(timezone.utc)

    title = fm.get("title") or fm.get("name", "")
    slug = slugify(title)[:60]
    fix_id = f"fix-{dt.strftime('%Y-%m-%d')}-{slug}"

    # Agent
    owner = fm.get("owner", "mnemosyne")
    agent = AGENT_MAP.get(owner, "mnemosyne")

    # Severity
    severity = determine_severity(fm, body)

    # Systems
    systems = determine_systems(fm)

    # Tags
    tags = fm.get("tags", [])

    # Status
    status = "resolved"

    # Source session
    source_session = fm.get("originSessionId", "")

    # Body extraction
    body_text = strip_frontmatter(body)
    sections = extract_body_sections(body_text)

    # Description as symptom fallback
    symptom = sections["symptom"] or fm.get("description", "")
    root_cause = sections["root_cause"] or ""
    solution = sections["solution"] or body_text[:500]

    # Build entry
    entry = {
        "id": fix_id,
        "date": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "agent": agent,
        "severity": severity,
        "status": status,
        "systems": systems,
        "tags": tags,
        "symptom": symptom[:1000] if symptom else "",
        "root_cause": root_cause[:2000] if root_cause else "",
        "solution": solution[:4000] if solution else "",
        "files_changed": sections["files_changed"],
        "detection_method": sections["detection_method"][:500] if sections["detection_method"] else "",
        "detection_agent": agent,
        "resolution_time_minutes": sections["resolution_time_minutes"],
        "related_fixes": [],
        "lessons_learned": sections["lessons_learned"][:2000] if sections["lessons_learned"] else "",
        "prevention": sections["prevention"][:1000] if sections["prevention"] else "",
        "extensions": {
            "source_file": filename,
            "source_title": title,
        },
        "source_session": source_session,
    }
    return entry


def write_entry(entry: dict, dry_run: bool = True):
    """Write a single registry entry as YAML-frontmatter markdown."""
    fix_path = DST_DIR / f"{entry['id']}.md"
    if dry_run:
        print(f"  [DRY-RUN] Would write: {fix_path.name}")
        return

    # Build YAML frontmatter
    yaml_front = yaml.dump(
        entry,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
        width=120
    )

    content = f"---\n{yaml_front}---\n"
    fix_path.write_text(content)
    print(f"  ✅ Wrote: {fix_path.name}")


def main():
    dry_run = "--exec" not in sys.argv

    if not SRC_DIR.exists():
        print(f"❌ Source dir not found: {SRC_DIR}")
        sys.exit(1)

    DST_DIR.mkdir(parents=True, exist_ok=True)

    # Find fix files
    fix_files = sorted(SRC_DIR.glob("*fix*.md"))
    print(f"Found {len(fix_files)} fix docs in {SRC_DIR}\n")

    entries = []

    for fpath in fix_files:
        print(f"📄 Processing: {fpath.name}")
        text = fpath.read_text()
        fm = parse_frontmatter(text)
        body = text
        entry = build_fix_entry(fm, body, fpath.name)
        entries.append(entry)
        write_entry(entry, dry_run=dry_run)
        print()

    if dry_run:
        print(f"\n⚠️  Dry-run complete. {len(entries)} entries would be created.")
        print(f"   Run with --exec to actually write files.")
    else:
        # Write migration report
        report_lines = [
            "# Migration Report — Phase 3",
            f"**Date:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            f"**Source:** {SRC_DIR}",
            f"**Destination:** {DST_DIR}",
            f"**Entries migrated:** {len(entries)}",
            "",
            "## Registry Entries",
        ]
        for entry in entries:
            report_lines.append(f"- `{entry['id']}` — {entry['symptom'][:80]}")
        report_lines.append("")
        report_lines.append("## Files to Archive")
        for fpath in fix_files:
            report_lines.append(f"- `{fpath.name}` → {ARCHIVE_DIR}/")
        report_lines.append("")
        report_lines.append("## Verification")
        report_lines.append("- [ ] All 14 entries reviewed by Mnemosyne (Phase 3.2)")
        report_lines.append("- [ ] Originais archived to .archived/ (Phase 3.3)")
        report_lines.append("- [ ] INDEX.md regenerated (Phase 3.4)")

        report_path = DST_DIR / "migration-report.md"
        report_path.write_text("\n".join(report_lines))
        print(f"\n📊 Report: {report_path}")

    # Summary
    print(f"\n{'='*60}")
    print(f"Summary: {len(entries)} entries")
    severities = {}
    agents = {}
    for e in entries:
        severities[e["severity"]] = severities.get(e["severity"], 0) + 1
        agents[e["agent"]] = agents.get(e["agent"], 0) + 1
    print(f"Severity: {severities}")
    print(f"Agents: {agents}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
