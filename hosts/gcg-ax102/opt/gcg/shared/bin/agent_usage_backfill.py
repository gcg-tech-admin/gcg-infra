#!/usr/bin/env python3
"""
agent_usage_backfill.py — Backfill miner for agent_usage observability.

Walks /opt/gcg/claude-profiles/*/.claude/projects/**/*.jsonl (Claude Code
session transcripts), extracts metadata-only rows, and upserts into
public.agent_usage on the staging DB.

Idempotent: upserts by src_uuid. Checkpointed by file mtime.
Metadata-only: never persists message content or client data.

Usage:
    GCG_DB_NAME=gcg_intelligence_staging python3 agent_usage_backfill.py
    GCG_DB_NAME=gcg_intelligence_staging python3 agent_usage_backfill.py --dry-run
    GCG_DB_NAME=gcg_intelligence_staging python3 agent_usage_backfill.py --reset-checkpoint

Build: Talos, 2026-06-16.  Spec: /opt/gcg/shared/plans/agent-usage-observability-2026-06-15.md
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, "/opt/gcg/shared/gcg_tools")
from db_connect import get_connection

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROFILES_DIR = "/opt/gcg/claude-profiles"
CHECKPOINT_FILE = "/opt/gcg/shared/state/agent_usage_checkpoint.json"
BATCH_SIZE = 500  # rows per DB commit
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"

# Buckets by token count (using output_tokens for assistant messages)
BUCKET_BOUNDS = [
    (50, "xs"),
    (200, "s"),
    (1000, "m"),
    (4000, "l"),
    (float("inf"), "xl"),
]

# Seat profile → (human_name, human_id) lookup
# human_id is best-effort (Telegram ID where known, else NULL)
SEAT_HUMAN_MAP = {
    "sub-peter": ("Peter Ivantsov", "418059105"),
    "sub-flore": ("Flore Narjoux", None),
    "sub-ishan": ("Ishan Parikh", None),
    "sub-olga": ("Olga Kuznetsova", None),
    "sub-pierre": ("Pierre Martens", "8396666548"),
    "sub-sergei": ("Sergei Ivanius", None),
    "sub-sonja": ("Sonja", None),  # verify
    "sub-vanessa": ("Vanessa Vaz", "7810071625"),
    "sub-vincent": ("Vincent Ribes", None),
    "sub-vlada": ("Vlada", None),
    "sub-maruf": ("Maruf Hasan", None),
}

# Agent → default workflow_class (deterministic tier 1)
AGENT_WORKFLOW_MAP = {
    "daen": "admin",
    "talos": "infra",
    "vulcan": "infra",
    "argus": "admin",
    "varys": "admin",
    "mnemosyne": "infra",
    "leon": "legal",
    "algaib": "legal",
    "kenji": "compliance",
    "malik": "banking",
    "alexa": "tax_advisory",
    "alex": "admin",
    "viktor": "admin",
    "max": "admin",
    "nik": "real_estate",
    "bob": "real_estate",
    "anna": "marketing",
    "vera": "capital_raising",
    "marcus": "sales",
    "jc": "admin",
    "angela": "admin",
    "goku": "infra",
    "socrates": "research",
    "niccolo": "research",
    "cassandra": "research",
    "confucius": "research",
    "nemesis": "infra",
    "chiron": "infra",
    "hector": "infra",
    "tom": "infra",
    "phil": "infra",
    "wonhoo": "infra",
    "yuri": "infra",
    "kira": "infra",
    "nemesis": "infra",
}

# Keyword → workflow_class (deterministic tier 1, checked on message content)
# Content is read only for classification, never persisted.
KEYWORD_WORKFLOW_MAP = [
    (r"\b(tax|vat|corporate tax|filing|return|fiscal)\b", "tax_advisory"),
    (r"\b(kyc|aml|compliance|due diligence|sanctions?)\b", "compliance"),
    (r"\b(contract|agreement|clause|nda|legal review|jurisdiction)\b", "legal"),
    (r"\b(bank|iban|swift|wire|account opening|beneficiary|kyb)\b", "banking"),
    (r"\b(invoice|payment|reconciliation|bookkeeping|ledger)\b", "tax_advisory"),
    (r"\b(proposal|quote|pricing|fee schedule|engagement)\b", "proposal"),
    (r"\b(visa|immigration|residency|passport|emirates id|medical)\b", "admin"),
    (r"\b(property|real estate|off[- ]?plan|mortgage|rental)\b", "real_estate"),
    (r"\b(marketing|campaign|social media|content|seo|ads?)\b", "marketing"),
    (r"\b(investor|fundraising|capital|pitch|deck)\b", "capital_raising"),
    (r"\b(draft|email|reply|write|message|respond)\b", "doc_drafting"),
    (r"\b(research|find|look up|search|what is|how does)\b", "research"),
    (r"\b(deploy|build|docker|git|database|fix|bug|error|code)\b", "infra"),
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger("agent_usage_backfill")


def derive_agent(project_name: str) -> str:
    """
    Derive agent name from project path component.
    Pattern: -opt-gcg-openclaw-{agent}-workspace{optional-suffix}
    Falls back to project basename with a warning.
    """
    # Primary pattern: /-opt-gcg-openclaw-{agent}-workspace*
    m = re.match(r"-opt-gcg-openclaw-([a-z0-9]+)-workspace.*", project_name)
    if m:
        return m.group(1)

    # Bridge sessions: -opt-gcg-shared-bridge-sessions-{name}
    m2 = re.match(r"-opt-gcg-shared-bridge-sessions-(.+)", project_name)
    if m2:
        return m2.group(1).replace("-", "_")

    # Fallback: non-agent path → junk, exclude from metrics
    log.warning("Agent derivation fallback for project: %s → __unknown__", project_name)
    return "__unknown__"


def token_bucket(token_count: int | None) -> str | None:
    """Coarse size bucket from token count. NULL-safe."""
    if token_count is None:
        return None
    for bound, label in BUCKET_BOUNDS:
        if token_count <= bound:
            return label
    return "xl"


def classify_workflow(agent: str, message_content: str | None) -> str | None:
    """
    Deterministic-first workflow classifier.
    Tier 1: agent identity → workflow map.
    Tier 2: keyword matching on message content (read-only, not persisted).
    Returns 'other' if unclassified.
    """
    # Tier 1: agent identity
    if agent in AGENT_WORKFLOW_MAP:
        return AGENT_WORKFLOW_MAP[agent]

    # Tier 2: keyword scan on message content (metadata-only determinant)
    if message_content:
        content_lower = message_content.lower()
        for pattern, wf_class in KEYWORD_WORKFLOW_MAP:
            if re.search(pattern, content_lower):
                return wf_class

    return "other"


def extract_message_text(msg_obj) -> str | None:
    """Safely extract text from message object (for classification only, never persisted)."""
    if not isinstance(msg_obj, dict):
        return str(msg_obj)[:500] if msg_obj else None
    content = msg_obj.get("content")
    if isinstance(content, str):
        return content[:500]
    if isinstance(content, list):
        # Take first text block
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if isinstance(text, str):
                    return text[:500]
    return None


def load_checkpoint() -> dict[str, float]:
    """Load file→mtime checkpoint. Returns empty dict if missing/corrupt."""
    if not os.path.exists(CHECKPOINT_FILE):
        return {}
    try:
        with open(CHECKPOINT_FILE) as f:
            data = json.load(f)
        return data.get("files", {}) if isinstance(data, dict) else {}
    except (json.JSONDecodeError, KeyError):
        log.warning("Corrupt checkpoint file, starting fresh")
        return {}


def save_checkpoint(checkpoint: dict[str, float]):
    """Persist checkpoint atomically."""
    os.makedirs(os.path.dirname(CHECKPOINT_FILE), exist_ok=True)
    tmp = CHECKPOINT_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"files": checkpoint, "updated": datetime.now(timezone.utc).isoformat()}, f)
    os.replace(tmp, CHECKPOINT_FILE)


def parse_line(line: str, seat_profile: str, agent: str) -> dict | None:
    """
    Parse a single transcript line. Returns row dict or None on skip/error.
    Per-line try/catch — one malformed line never aborts a file.
    """
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None

    msg_type = obj.get("type")
    if msg_type not in ("user", "assistant"):
        return None

    uuid = obj.get("uuid")
    if not uuid:
        return None

    ts_raw = obj.get("timestamp")
    if ts_raw:
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            ts = datetime.now(timezone.utc)
    else:
        ts = datetime.now(timezone.utc)

    session_id = obj.get("sessionId")
    direction = "inbound" if msg_type == "user" else "outbound"

    # Token count: only available from assistant message.usage
    token_count = None
    if msg_type == "assistant":
        msg_obj = obj.get("message", {})
        if isinstance(msg_obj, dict):
            usage = msg_obj.get("usage", {})
            if isinstance(usage, dict):
                token_count = usage.get("output_tokens")

    msg_len_bucket = token_bucket(token_count)

    # Workflow classification: scan user message content (read-only)
    workflow_class = None
    if msg_type == "user":
        msg_obj = obj.get("message", {})
        text = extract_message_text(msg_obj)
        workflow_class = classify_workflow(agent, text)
    else:
        # Outbound inherits agent's default workflow
        workflow_class = AGENT_WORKFLOW_MAP.get(agent, "other")

    return {
        "ts": ts,
        "agent": agent,
        "seat_profile": seat_profile,
        "human_id": SEAT_HUMAN_MAP.get(seat_profile, (None, None))[1],
        "human_name": SEAT_HUMAN_MAP.get(seat_profile, (None, None))[0],
        "channel": "cli",  # Claude Code is always CLI
        "direction": direction,
        "session_id": session_id,
        "msg_len_bucket": msg_len_bucket,
        "workflow_class": workflow_class,
        "source": "backfill",
        "src_uuid": uuid,
    }


def upsert_batch(conn, rows: list[dict]) -> int:
    """
    Upsert a batch of rows by src_uuid. Returns count inserted/updated.
    Uses INSERT...ON CONFLICT for idempotency.
    """
    if not rows:
        return 0

    cur = conn.cursor()
    sql = """
    INSERT INTO public.agent_usage (
        ts, agent, seat_profile, human_id, human_name, channel,
        direction, session_id, msg_len_bucket, workflow_class,
        source, src_uuid
    ) VALUES (
        %(ts)s, %(agent)s, %(seat_profile)s, %(human_id)s, %(human_name)s, %(channel)s,
        %(direction)s, %(session_id)s, %(msg_len_bucket)s, %(workflow_class)s,
        %(source)s, %(src_uuid)s
    )
    ON CONFLICT (src_uuid) DO NOTHING
    """
    try:
        cur.executemany(sql, rows)
        inserted = cur.rowcount
        conn.commit()
        return inserted
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def find_transcript_files() -> list[str]:
    """Find all transcript JSONL files under Claude profiles."""
    pattern = os.path.join(PROFILES_DIR, "*", ".claude", "projects", "*", "*.jsonl")
    import glob
    files = sorted(glob.glob(pattern))
    log.info("Found %d transcript files", len(files))
    return files


def main():
    parser = argparse.ArgumentParser(description="Backfill agent_usage from Claude Code transcripts")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, no DB writes")
    parser.add_argument("--reset-checkpoint", action="store_true", help="Re-scan all files")
    parser.add_argument("--limit", type=int, default=0, help="Limit files processed (0=all)")
    args = parser.parse_args()

    start_time = time.time()
    checkpoint = {} if args.reset_checkpoint else load_checkpoint()

    files = find_transcript_files()
    if args.limit:
        files = files[: args.limit]

    conn = None if args.dry_run else get_connection(agent_name="talos")
    total_inserted = 0
    files_processed = 0
    files_skipped = 0
    lines_processed = 0
    lines_error = 0

    for filepath in files:
        try:
            mtime = os.path.getmtime(filepath)
        except OSError:
            continue

        # Checkpoint: skip unchanged files
        prev_mtime = checkpoint.get(filepath)
        if prev_mtime is not None and mtime <= prev_mtime:
            files_skipped += 1
            continue

        # Derive seat_profile and agent from path
        rel = os.path.relpath(filepath, PROFILES_DIR)
        parts = rel.split(os.sep)
        seat_profile = parts[0]  # e.g., "sub-peter"
        project_name = parts[3]  # e.g., "-opt-gcg-openclaw-talos-workspace"
        agent = derive_agent(project_name)

        batch: list[dict] = []
        file_lines = 0
        file_inserted = 0

        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    lines_processed += 1
                    file_lines += 1

                    try:
                        row = parse_line(line, seat_profile, agent)
                    except Exception:
                        lines_error += 1
                        continue

                    if row is None:
                        continue

                    batch.append(row)
                    if len(batch) >= BATCH_SIZE:
                        if not args.dry_run:
                            file_inserted += upsert_batch(conn, batch)
                        batch.clear()

            # Final flush
            if batch and not args.dry_run:
                file_inserted += upsert_batch(conn, batch)

            # Update checkpoint for this file
            checkpoint[filepath] = mtime
            files_processed += 1
            total_inserted += file_inserted

            if files_processed % 50 == 0:
                save_checkpoint(checkpoint)
                elapsed = time.time() - start_time
                rate = total_inserted / elapsed if elapsed > 0 else 0
                log.info(
                    "Progress: %d files, %d inserted, %d skipped, %d lines, %.0f rows/s",
                    files_processed,
                    total_inserted,
                    files_skipped,
                    lines_processed,
                    rate,
                )

        except Exception:
            log.exception("Error processing file: %s", filepath)
            continue

    # Final checkpoint
    save_checkpoint(checkpoint)

    elapsed = time.time() - start_time
    log.info(
        "DONE: %d files processed, %d skipped, %d rows inserted, %d lines, %d errors, %.1fs",
        files_processed,
        files_skipped,
        total_inserted,
        lines_processed,
        lines_error,
        elapsed,
    )

    if conn:
        conn.close()


if __name__ == "__main__":
    main()
