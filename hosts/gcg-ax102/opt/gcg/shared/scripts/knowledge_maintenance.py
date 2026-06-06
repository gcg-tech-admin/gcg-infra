#!/usr/bin/env python3
import sys
import fcntl
import os
import atexit

# Acquire lock to prevent concurrent runs
LOCKFILE = "/var/lock/gcg-knowledge-maintenance.lock"
lock_f = open(LOCKFILE, "w")
atexit.register(lambda: lock_f.close())
try:
    fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
except (IOError, OSError):
    lock_f.close()
    print("Another instance is running. Exiting.")
    sys.exit(0)

"""
knowledge_maintenance.py — KB maintenance for GCG Knowledge Base (runs every 15min via cron).

Responsibilities:
  (a) Scan /opt/gcg/shared/docs/ recursively for .md files not yet in pgvector
  (b) Auto-embed new/changed files (checksum-based diff), storing embedding_model metadata
  (c) Purge pgvector embeddings for files that no longer exist on disk
  (d) Generate KNOWLEDGE_INDEX.md FROM pgvector (derived artifact, never manually edited)
  (e) Time-based staleness detection per-category
  (e2) Taxonomy enforcement before embedding — validate folder is in approved list
  (f) Gap detection: cross-reference completed cascade plans vs cascade-artifacts/
  (g) Emit /opt/gcg/shared/logs/last_run.json with metrics
  (h) Cost ceiling: abort + alert if estimated embed cost > $2/run
  (i) Failure handling: log to embed_failures.json with retry queue (max 3 retries)
  (j) Alert via fleet to daen on failures, gaps, cost breach, >5 stale docs
  (k) Durable remediation backlog: kb_remediation_backlog.jsonl
  (l) 200-doc threshold warning

Usage:
  python3 knowledge_maintenance.py              # full run
  python3 knowledge_maintenance.py --dry-run    # scan only, no writes
  python3 knowledge_maintenance.py --health     # emit JSON health status
"""

import os
import json
import hashlib
import argparse
import subprocess
import re
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

log = logging.getLogger("knowledge_maintenance")
log.addHandler(logging.StreamHandler(sys.stderr))
log.setLevel(logging.WARNING)


def chunk_text(text, chunk_size=2000, overlap=200):
    """Split text into overlapping chunks."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk.strip())
        start = end - overlap
    return chunks

# ── Paths ──────────────────────────────────────────────────────────────────────
DOCS_ROOT = "/opt/gcg/shared/docs"
HANDOFFS_ROOT = "/opt/gcg/shared/handoffs"


def _walk_all_docs():
    """Walk DOCS_ROOT and HANDOFFS_ROOT in order. Skip missing roots."""
    for _root in (DOCS_ROOT, HANDOFFS_ROOT):
        if not os.path.isdir(_root):
            continue
        for _r, _d, _f in os.walk(_root):
            yield _r, _d, _f
SCRIPTS_DIR = "/opt/gcg/shared/scripts"
LOGS_DIR = "/opt/gcg/shared/logs"
KNOWLEDGE_INDEX_PATH = "/opt/gcg/shared/docs/KNOWLEDGE_INDEX.md"
LAST_RUN_PATH = "/opt/gcg/shared/logs/last_run.json"
EMBED_FAILURES_PATH = "/opt/gcg/shared/logs/embed_failures.json"
REMEDIATION_BACKLOG_PATH = "/opt/gcg/shared/logs/kb_remediation_backlog.jsonl"
CASCADE_ACTIVE_PLANS_PATH = "/opt/gcg/shared/cascade_active_plans.json"
CASCADE_ARTIFACTS_DIR = "/opt/gcg/shared/docs/reference/cascade-artifacts"
FLEET_BIN = "/opt/gcg/shared/bin/fleet"
MEMORY_BIN = "/opt/gcg/shared/bin/memory"

# ── Taxonomy ───────────────────────────────────────────────────────────────────
# Canonical 4-bucket ontology (DOC_ONTOLOGY_CANONICAL.md v1.2)
# Canonical 5-bucket ontology (DOC_ONTOLOGY_CANONICAL.md v2.0)
CANONICAL_BUCKETS = {"architecture", "reference", "decisions", "record", "plans", "handoffs"}

# Deprecated buckets: still indexed but flagged for migration
DEPRECATED_BUCKETS = {
    # These dirs still exist on disk — indexed but logged for migration
    "compliance",    # → reference/
    "council",       # → decisions/
    "runbooks",      # → reference/
    "sops",          # → reference/
    "research",      # → record/
    "reviews",       # → record/
    "from-macmini",  # → triage to appropriate bucket
    "fleet",         # → architecture/ or reference/
    "rollback_refs", # → reference/
    "strategies",    # → plans/
    "proposals",     # → plans/
    "playbooks",     # → reference/
    "guides",        # → reference/
}

APPROVED_TAXONOMY = CANONICAL_BUCKETS | DEPRECATED_BUCKETS

# ── Staleness thresholds (days, 0 = no expiry) ────────────────────────────────
# Staleness per DOC_ONTOLOGY_CANONICAL.md v2.0 (5 buckets)
STALENESS_DAYS = {
    "architecture": 0,   # evergreen — replace in place
    "reference":    0,   # evergreen
    "decisions":    0,   # append-only, immutable — no expiry
    "record":       0,   # frozen at write-time — no expiry
    "plans":        90,  # in-flight specs — flag if stale >90 days
    "handoffs":     30,  # session artifacts — archive after 30 days
    # Deprecated dirs (pending migration to canonical 5)
    "reviews": 0, "research": 0, "runbooks": 90, "sops": 90,
    "decisions_old": 0, "compliance": 365, "strategies": 90,
    "proposals": 60, "playbooks": 180, "guides": 180, "council": 0,
}

# ── Gate 2: Excluded path patterns ───────────────────────────────────────────
EXCLUDED_PATTERNS = [
    "superseded/",
    "archive/",
    "decisions/archive/",
    # Root-level generated index files
    "DOC_INDEX.md",
    "MAP.md",
]

# ── Gate 1: Per-folder index defaults ─────────────────────────────────────────
# False = must have `index: true` in frontmatter to be embedded
FOLDER_INDEX_DEFAULTS = {
    # Canonical 5 — always indexed
    "architecture": True, "reference": True, "decisions": True,
    "record": True, "plans": True, "handoffs": True,
    # Deprecated dirs — still indexed pending migration
    "reviews": True, "research": True, "council": True,
    "sops": True, "runbooks": True, "guides": True,
    "playbooks": True, "compliance": True, "fleet": True,
    "rollback_refs": True, "from-macmini": True,
    # Approval-gated (require frontmatter index:true + status:approved)
    "proposals": False, "strategies": False,
}
APPROVAL_REQUIRED_FOLDERS = {"proposals", "strategies"}

# ── Gate 3: Semantic dedup ─────────────────────────────────────────────────────
DEDUP_SIMILARITY_THRESHOLD = 0.85
DEDUP_PREVIEW_CHARS = 500

# ── Cost ceiling ──────────────────────────────────────────────────────────────
COST_CEIL_USD = 2.0
# text-embedding-3-small pricing: $0.02 per 1M tokens
# Rough estimate: 1 token ≈ 4 chars of text
COST_PER_CHAR = 0.02 / (1_000_000 * 4)  # $0.000000005 per char
MAX_RETRIES = 3

# ── Embedding model identifier ─────────────────────────────────────────────────
EMBEDDING_MODEL = "text-embedding-3-small"


# ── DB connection ──────────────────────────────────────────────────────────────
def get_db():
    sys.path.insert(0, "/opt/gcg/shared/gcg_tools")
    from db_connect import get_connection
    return get_connection(admin=True)


# ── Helpers ────────────────────────────────────────────────────────────────────
def file_checksum(path: str) -> str:
    """SHA-256 checksum of file contents."""
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception:
        return ""


def get_category(path: str) -> str:
    """GETCAT_PATCH_2026_05_21 — Extract category from doc path.
    Handles HANDOFFS_ROOT (returns 'handoffs') and root-level symlinks
    (resolves to target before classifying)."""
    # Resolve symlinks so root-level redirect symlinks classify by their target
    try:
        resolved = os.path.realpath(path)
    except Exception:
        resolved = path

    # Handoffs root: anything under /opt/gcg/shared/handoffs/ is bucket "handoffs"
    if resolved.startswith(HANDOFFS_ROOT + os.sep) or resolved == HANDOFFS_ROOT:
        return "handoffs"

    # Docs root: first path component under DOCS_ROOT is the category
    if resolved.startswith(DOCS_ROOT + os.sep):
        rel = os.path.relpath(resolved, DOCS_ROOT)
        parts = rel.split(os.sep)
        # If the file is directly at docs/ root (no subdir), there's no category
        if len(parts) <= 1:
            return "unknown"
        return parts[0]

    return "unknown"


def is_excluded_path(path: str) -> bool:
    """Gate 2: Return True if path is under a superseded/ or archive/ subdir."""
    norm = path.replace(os.sep, "/")
    return any(pat in norm for pat in EXCLUDED_PATTERNS)


def parse_frontmatter(path: str) -> dict:
    """Gate 1: Parse YAML frontmatter between --- delimiters. Returns {} if none."""
    try:
        with open(path, "r", errors="replace") as f:
            content = f.read(2048)
        if not content.startswith("---"):
            return {}
        end = content.find("\n---", 3)
        if end == -1:
            return {}
        fm_text = content[3:end].strip()
        result = {}
        for line in fm_text.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                result[k.strip().lower()] = v.strip().lower()
        return result
    except Exception:
        return {}


def should_index_file(path: str) -> tuple:
    """Gate 1: Return (should_index: bool, reason: str).
    Approval-required folders need index: true + status: approved in frontmatter.
    Other folders default to FOLDER_INDEX_DEFAULTS; can override with index: false.
    """
    cat = get_category(path)
    fm = parse_frontmatter(path)
    index_flag = fm.get("index", "")
    status_flag = fm.get("status", "")

    if cat in APPROVAL_REQUIRED_FOLDERS:
        if index_flag == "true" and status_flag == "approved":
            return True, "frontmatter approved"
        if index_flag == "true" and status_flag != "approved":
            return False, f"index: true but status is '{status_flag}' (need 'approved')"
        return False, f"proposals/strategies require index: true + status: approved"

    # Other folders: use folder default, allow explicit override
    folder_default = FOLDER_INDEX_DEFAULTS.get(cat, True)
    if index_flag == "false":
        return False, "index: false in frontmatter"
    if index_flag == "true":
        return True, "index: true in frontmatter"
    # No frontmatter flag — use folder default
    if folder_default:
        return True, f"folder default (auto-index)"
    return False, f"folder default (no-index)"


def check_semantic_duplicate(path: str, conn) -> tuple:
    """Gate 3: Check for near-duplicate in KB using pg_trgm similarity on content.
    Returns (is_duplicate: bool, matching_hash: str).
    Skips if preview too short or trgm unavailable.
    """
    try:
        with open(path, "r", errors="replace") as f:
            preview = f.read(DEDUP_PREVIEW_CHARS).strip()
        if len(preview) < 50:
            return False, ""
        cur = conn.cursor()
        cur.execute("""
            SELECT content_hash,
                   similarity(content, %s) AS sim
            FROM memories
            WHERE legacy_source_type = 'knowledge'
              AND scope = 'shared_fleet'
              AND length(content) > 50
              AND similarity(content, %s) > %s
            ORDER BY sim DESC
            LIMIT 1
        """, (preview, preview, DEDUP_SIMILARITY_THRESHOLD))
        row = cur.fetchone()
        if row:
            return True, row[0]
    except Exception as e:
        print(f"[WARN] dedup check failed for {path}: {e}", file=sys.stderr)
    return False, ""


def is_taxonomy_valid(path: str) -> bool:
    """Return True if path is under an approved taxonomy folder.
    Canonical 4 = clean. Deprecated = valid but logged for migration."""
    return get_category(path) in APPROVED_TAXONOMY


def is_deprecated_bucket(path: str) -> bool:
    """True if file is in a deprecated pre-consolidation bucket."""
    return get_category(path) in DEPRECATED_BUCKETS


def extract_summary(chunk_text: str) -> str:
    """Extract first markdown heading or first 100 chars from chunk text."""
    lines = chunk_text.strip().splitlines()
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()[:120]
    return chunk_text.strip()[:100].replace("\n", " ")


def fleet_alert(message: str, dry_run: bool = False):
    """Send alert to daen via fleet."""
    if dry_run:
        print(f"[DRY-RUN] Would fleet wake daen: {message}")
        return
    try:
        subprocess.run(
            [FLEET_BIN, "wake", "daen", message],
            capture_output=True, timeout=15
        )
    except Exception as e:
        print(f"[WARN] Fleet alert failed: {e}", file=sys.stderr)


def append_remediation_backlog(entry: dict):
    """Append one issue to the durable remediation backlog with dedup.

    Skips append if an unresolved entry for the same (type, path) already exists.
    """
    os.makedirs(LOGS_DIR, exist_ok=True)
    entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    key = (entry.get("type"), entry.get("path"))
    # Check if an unresolved duplicate already exists
    if os.path.exists(REMEDIATION_BACKLOG_PATH):
        try:
            with open(REMEDIATION_BACKLOG_PATH) as f:
                for line in f:
                    try:
                        existing = json.loads(line.strip())
                        if (existing.get("type"), existing.get("path")) == key and not existing.get("resolved"):
                            return  # duplicate — skip
                    except Exception:
                        pass
        except Exception:
            pass
    with open(REMEDIATION_BACKLOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


def count_open_backlog() -> int:
    """Count open (unresolved) items in remediation backlog."""
    if not os.path.exists(REMEDIATION_BACKLOG_PATH):
        return 0
    count = 0
    try:
        with open(REMEDIATION_BACKLOG_PATH) as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if not entry.get("resolved", False):
                        count += 1
                except Exception:
                    pass
    except Exception:
        pass
    return count


def load_embed_failures() -> list:
    """Load current failure queue."""
    if not os.path.exists(EMBED_FAILURES_PATH):
        return []
    try:
        with open(EMBED_FAILURES_PATH) as f:
            return json.load(f)
    except Exception:
        return []


def save_embed_failures(failures: list):
    """Persist failure queue."""
    os.makedirs(LOGS_DIR, exist_ok=True)
    with open(EMBED_FAILURES_PATH, "w") as f:
        json.dump(failures, f, indent=2)


def log_embed_failure(path: str, error: str, failures: list) -> list:
    """Add or update failure entry. Cap retries at MAX_RETRIES."""
    for entry in failures:
        if entry["path"] == path:
            entry["retries"] = entry.get("retries", 0) + 1
            entry["last_error"] = error
            entry["last_attempt"] = datetime.now(timezone.utc).isoformat()
            return failures
    failures.append({
        "path": path,
        "retries": 1,
        "last_error": error,
        "last_attempt": datetime.now(timezone.utc).isoformat(),
        "resolved": False,
    })
    return failures


def should_retry(entry: dict) -> bool:
    return entry.get("retries", 0) < MAX_RETRIES and not entry.get("resolved", False)


# ── DB Operations ──────────────────────────────────────────────────────────────
def get_embedded_files(conn) -> dict:
    """Return {source_path: meta} for KB docs already in memories (hash-matched)."""
    cur = conn.cursor()
    cur.execute("""
        SELECT content_hash, MAX(created_at) AS updated_at
        FROM memories
        WHERE legacy_source_type = 'knowledge'
          AND scope = 'shared_fleet'
        GROUP BY content_hash
    """)
    hash_to_meta = {row[0]: {"content_hash": row[0], "updated_at": row[1]}
                    for row in cur.fetchall()}
    # Map back to file paths by computing hashes on disk
    result = {}
    for root, dirs, files in _walk_all_docs():
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fn in files:
            if fn.endswith(".md") and fn != "KNOWLEDGE_INDEX.md":
                p = os.path.join(root, fn)
                h = file_checksum(p)
                if h and h in hash_to_meta:
                    result[p] = hash_to_meta[h]
    return result


def get_db_stored_hash(conn, source_path: str) -> str:
    """Return stored file_hash for source_path, or None if not embedded yet.
    Primary: knowledge_sources (exact path tracker).
    Fallback: check if current file hash exists in memories (handles legacy embeds)."""
    cur = conn.cursor()
    cur.execute(
        "SELECT file_hash FROM knowledge_sources WHERE source_path = %s AND source_type = 'knowledge'",
        (source_path,)
    )
    row = cur.fetchone()
    if row:
        return row[0]
    try:
        current_hash = file_checksum(source_path)
        if not current_hash:
            return None
        sp = conn.cursor()
        sp.execute("SAVEPOINT get_stored_hash_sp")
        cur.execute(
            "SELECT id FROM memories WHERE content_hash = %s AND legacy_source_type = 'knowledge' LIMIT 1",
            (current_hash,)
        )
        if cur.fetchone():
            try:
                cur.execute(
                    "INSERT INTO knowledge_sources (source_type, source_path, file_hash, updated_at) VALUES ('knowledge', %s, %s, NOW()) ON CONFLICT (source_type, source_path) DO UPDATE SET file_hash = EXCLUDED.file_hash, updated_at = EXCLUDED.updated_at",
                    (source_path, current_hash)
                )
                conn.commit()
            except Exception as ke:
                log.warning("knowledge_sources update failed in get_db_stored_hash for %s: %s", source_path, ke)
                conn.rollback()
            return current_hash
        sp.execute("RELEASE SAVEPOINT get_stored_hash_sp")
    except Exception as e:
        log.warning("get_db_stored_hash fallback failed for %s: %s", source_path, e)
        conn.rollback()
    return None


def purge_embeddings(conn, source_path: str, dry_run: bool = False) -> int:
    """Delete memories for a path (matched by content_hash of file on disk). Returns count deleted."""
    h = file_checksum(source_path)
    if not h:
        return 0
    cur = conn.cursor()
    if dry_run:
        cur.execute("SELECT COUNT(*) FROM memories WHERE content_hash = %s AND legacy_source_type = 'knowledge'", (h,))
        return cur.fetchone()[0]
    cur.execute("DELETE FROM memories WHERE content_hash = %s AND legacy_source_type = 'knowledge'", (h,))
    deleted = cur.rowcount
    conn.commit()
    return deleted


def embed_file(path: str, dry_run: bool = False, conn=None) -> bool:
    """FILE_PATCH_2026_05_21 — Read file content, chunk if large, embed each chunk via
    the memory CLI (`memory capture <text>` — positional, no --file flag).
    Returns True on success. Also updates knowledge_sources to track embedded hash."""
    if dry_run:
        print(f"[DRY-RUN] Would embed: {path}")
        return True
    # Read file
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception as e:
        print(f"[ERROR] embed read failed for {path}: {e}", file=sys.stderr)
        return False
    if not text.strip():
        return True  # empty file — nothing to embed, not an error

    # Chunk if large (>3500 chars to stay within embedding-window comfort)
    chunks = chunk_text(text, chunk_size=3500, overlap=300) if len(text) > 3500 else [text]

    # Embed each chunk
    succeeded = 0
    failed = 0
    for i, chunk in enumerate(chunks):
        # Prefix chunk with provenance so the embedded row knows its source
        prefixed = f"[source: {path} chunk {i+1}/{len(chunks)}]\n\n{chunk}"
        try:
            result = subprocess.run(
                [
                    MEMORY_BIN, "capture", prefixed,
                    "--agent", "talos",
                    "--source-type", "knowledge",
                    "--scope", "shared_fleet",
                ],
                capture_output=True, text=True, timeout=600
            )
            if result.returncode != 0:
                failed += 1
                print(f"[ERROR] memory capture failed for {path} chunk {i+1}/{len(chunks)}: {result.stderr[:200]}", file=sys.stderr)
            else:
                succeeded += 1
        except Exception as e:
            failed += 1
            print(f"[ERROR] embed exception for {path} chunk {i+1}/{len(chunks)}: {e}", file=sys.stderr)

    if succeeded == 0:
        return False

    # Update knowledge_sources so get_db_stored_hash tracks this embed
    if conn is not None:
        try:
            current_hash = file_checksum(path)
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO knowledge_sources (source_type, source_path, file_hash, updated_at)
                VALUES ('knowledge', %s, %s, NOW())
                ON CONFLICT (source_type, source_path) DO UPDATE
                  SET file_hash = EXCLUDED.file_hash,
                      updated_at = EXCLUDED.updated_at
            """, (path, current_hash))
            conn.commit()
        except Exception as ke:
            print(f"[WARN] knowledge_sources update failed for {path}: {ke}", file=sys.stderr)
            conn.rollback()
    return failed == 0
def get_all_doc_chunks(conn) -> dict:
    """Return {source_path: {"summary", "category", "updated_at"}} for all docs in pgvector.

    Uses knowledge_sources as authoritative path index.
    Falls back to hash-matching for legacy entries not yet in knowledge_sources.
    """
    docs = {}
    cur = conn.cursor()

    # Primary path: knowledge_sources maps source_path → what was embedded
    cur.execute("""
        SELECT source_path, updated_at
        FROM knowledge_sources
        WHERE source_type = 'knowledge'
        ORDER BY updated_at DESC
    """)
    ks_paths = {row[0]: row[1] for row in cur.fetchall()}

    for source_path, updated_at in ks_paths.items():
        if not os.path.exists(source_path):
            continue
        try:
            with open(source_path) as fp:
                content = fp.read()
            chunks = chunk_text(content)
            first_chunk = chunks[0] if chunks else content[:500]
        except Exception as e:
            log.warning("get_all_doc_chunks: cannot read %s: %s", source_path, e)
            first_chunk = os.path.basename(source_path)
        summary = extract_summary(first_chunk)
        cat = get_category(source_path)
        docs[source_path] = {
            "summary": summary,
            "category": cat,
            "updated_at": updated_at,
        }

    # Fallback: hash-match for legacy embeds not yet in knowledge_sources
    cur.execute("""
        SELECT DISTINCT content_hash, MAX(created_at) AS max_created_at
        FROM memories
        WHERE legacy_source_type = 'knowledge' AND scope = 'shared_fleet'
        GROUP BY content_hash
        ORDER BY max_created_at DESC
    """)
    memories_by_hash = {row[0]: row[1] for row in cur.fetchall()}

    for root, dirs, files in _walk_all_docs():
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fn in files:
            if fn.endswith(".md") and fn != "KNOWLEDGE_INDEX.md":
                full = os.path.join(root, fn)
                if full in docs:
                    continue  # already found via knowledge_sources
                try:
                    with open(full, "rb") as fb:
                        file_hash = hashlib.sha256(fb.read()).hexdigest()
                except Exception:
                    continue
                if file_hash in memories_by_hash:
                    updated_at = memories_by_hash[file_hash]
                    try:
                        with open(full) as fp:
                            content = fp.read()
                        chunks = chunk_text(content)
                        first_chunk = chunks[0] if chunks else content[:500]
                    except Exception as e:
                        log.warning("get_all_doc_chunks fallback: cannot read %s: %s", full, e)
                        first_chunk = os.path.basename(full)
                    summary = extract_summary(first_chunk)
                    cat = get_category(full)
                    docs[full] = {
                        "summary": summary,
                        "category": cat,
                        "updated_at": updated_at,
                    }

    return docs


def get_file_mtime(path: str):
    """Return mtime as datetime (UTC)."""
    try:
        return datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
    except Exception:
        return None


def check_staleness(path: str, category: str) -> bool:
    """Return True if file is stale based on category threshold."""
    threshold = STALENESS_DAYS.get(category, 0)
    if threshold == 0:
        return False
    mtime = get_file_mtime(path)
    if mtime is None:
        return False
    age_days = (datetime.now(timezone.utc) - mtime).days
    return age_days > threshold


# ── Index Generation ───────────────────────────────────────────────────────────
def generate_index(docs: dict, dry_run: bool = False) -> int:
    """
    Generate KNOWLEDGE_INDEX.md from pgvector doc metadata.
    Groups by category, extracts summary from first chunk.
    Returns number of entries written.
    """
    # Group by category
    by_category: dict = {}
    for path, meta in sorted(docs.items()):
        cat = meta["category"]
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append((path, meta))

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"<!-- AUTO-GENERATED by knowledge_maintenance.py — DO NOT EDIT MANUALLY -->",
        f"# GCG Knowledge Index",
        f"**Generated:** {now_str} | **Source:** pgvector knowledge_embeddings | **Owner:** Mnemosyne",
        f"",
        f"> This file is auto-generated weekly from PostgreSQL. For real-time search, use `recall_v2.py`.",
        f"> Total documents: {len(docs)}",
        f"",
        "---",
        "",
    ]

    for cat in sorted(by_category.keys()):
        entries = by_category[cat]
        lines.append(f"## {cat.capitalize()}")
        lines.append("")
        for path, meta in sorted(entries, key=lambda x: x[0]):
            rel_path = os.path.relpath(path, "/opt/gcg/shared")
            summary = meta["summary"]
            updated = meta["updated_at"]
            date_str = updated.strftime("%Y-%m-%d") if updated else "unknown"
            lines.append(f"- **[{os.path.basename(path)}](/{rel_path})** — {summary} *(updated {date_str})*")
        lines.append("")

    content = "\n".join(lines)

    if dry_run:
        print(f"[DRY-RUN] Would write KNOWLEDGE_INDEX.md ({len(docs)} entries)")
        return len(docs)

    os.makedirs(os.path.dirname(KNOWLEDGE_INDEX_PATH), exist_ok=True)
    with open(KNOWLEDGE_INDEX_PATH, "w") as f:
        f.write(content)

    return len(docs)


# ── Gap Detection ──────────────────────────────────────────────────────────────
def detect_gaps() -> list:
    """
    Cross-reference completed cascade plans against docs/reference/cascade-artifacts/.
    Returns list of {plan_name, plan_id, missing_artifact} for gaps.
    """
    gaps = []
    if not os.path.exists(CASCADE_ACTIVE_PLANS_PATH):
        return gaps

    try:
        with open(CASCADE_ACTIVE_PLANS_PATH) as f:
            plans_data = json.load(f)
    except Exception:
        return gaps

    # Support both array format (cascade_active_plans.json) and dict formats
    if isinstance(plans_data, dict):
        if "plans" in plans_data:
            plans = plans_data["plans"]
        else:
            plans = list(plans_data.values())
    elif isinstance(plans_data, list):
        plans = plans_data
    else:
        return gaps

    artifacts_dir = CASCADE_ARTIFACTS_DIR
    existing_artifacts = set()
    if os.path.isdir(artifacts_dir):
        for f in os.listdir(artifacts_dir):
            existing_artifacts.add(f.lower().rstrip(".md").replace("-", "_"))

    for plan in plans:
        name = plan.get("name") or plan.get("plan_name", "unknown")
        plan_id = plan.get("registry_id") or plan.get("first_task_gid") or "unknown"
        status = plan.get("status", "")

        # Only check completed plans
        if status not in ("complete", "completed", "done"):
            continue

        # Derive expected artifact filename from plan name
        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        if slug not in existing_artifacts:
            # Check fuzzy match
            fuzzy_match = any(slug[:20] in art for art in existing_artifacts)
            if not fuzzy_match:
                gaps.append({
                    "plan_name": name,
                    "plan_id": plan_id,
                    "expected_slug": slug,
                    "missing_artifact": True,
                })

    return gaps


# ── Main ───────────────────────────────────────────────────────────────────────
def run(dry_run: bool = False):
    os.makedirs(LOGS_DIR, exist_ok=True)
    os.makedirs(SCRIPTS_DIR, exist_ok=True)

    metrics = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "docs_scanned": 0,
        "docs_added": 0,
        "docs_pruned": 0,
        "docs_stale": [],
        "embed_successes": 0,
        "embed_failures": 0,
        "cost_estimate_usd": 0.0,
        "gaps": [],
        "index_entries_generated": 0,
        "embedding_model": EMBEDDING_MODEL,
        "taxonomy_violations": [],
        "threshold_200_warning": False,
        "docs_skipped_excluded": 0,
        "docs_skipped_index_gate": 0,
        "docs_skipped_dedup": 0,
    }

    # ── Connect ────────────────────────────────────────────────────────────────
    try:
        conn = get_db()
    except Exception as e:
        print(f"[FATAL] DB connection failed: {e}", file=sys.stderr)
        sys.exit(1)

    # ── Load failure queue ─────────────────────────────────────────────────────
    failures = load_embed_failures()

    # ── Scan disk ──────────────────────────────────────────────────────────────
    all_doc_paths = []
    for root, dirs, files in _walk_all_docs():
        # Skip hidden dirs
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fn in files:
            if fn.endswith(".md"):
                full = os.path.join(root, fn)
                all_doc_paths.append(full)

    metrics["docs_scanned"] = len(all_doc_paths)

    # 200-doc threshold warning
    if len(all_doc_paths) >= 200:
        metrics["threshold_200_warning"] = True
        trgm_index_present = False
        try:
            with conn.cursor() as _cur:
                _cur.execute(
                    "SELECT 1 FROM pg_indexes "
                    "WHERE tablename LIKE %s "
                    "AND (indexdef ILIKE %s OR indexdef ILIKE %s) "
                    "LIMIT 1",
                    ('memories%', '%gin_trgm%', '%gist_trgm%'),
                )
                trgm_index_present = _cur.fetchone() is not None
        except Exception as _e:
            # If the check itself fails, don't spam — fail closed on the alert.
            print(f"[WARN] trgm-index check failed, suppressing 200-doc alert: {_e}", file=sys.stderr)
            trgm_index_present = True
        metrics["trgm_index_present"] = trgm_index_present
        if not trgm_index_present:
            msg = (
                f"⚠️ KB doc count reached {len(all_doc_paths)} (threshold: 200) "
                "AND trigram index missing on memories*. "
                "Hybrid recall is degraded — restore pg_trgm index or rebuild."
            )
            fleet_alert(msg, dry_run=dry_run)

    # ── Get currently embedded files ───────────────────────────────────────────
    embedded = get_embedded_files(conn)
    embedded_paths = set(embedded.keys())
    disk_paths = set(all_doc_paths)

    # ── (c) Purge embeddings for deleted files ─────────────────────────────────
    to_purge = embedded_paths - disk_paths
    pruned_count = 0
    for path in to_purge:
        deleted = purge_embeddings(conn, path, dry_run=dry_run)
        if not dry_run:
            pruned_count += deleted
            print(f"[PURGE] Removed {deleted} chunks for deleted file: {path}")
        else:
            pruned_count += 1
    metrics["docs_pruned"] = len(to_purge)

    # ── Cost estimation ─────────────────────────────────────────────────────────
    # Estimate which files need embedding
    files_to_embed = []
    stale_docs = []
    taxonomy_violations = []

    for path in all_doc_paths:
        # Skip KNOWLEDGE_INDEX.md — it's the auto-generated output, not a doc to embed/validate
        if os.path.basename(path) == "KNOWLEDGE_INDEX.md":
            continue

        # ── Gate 2: Exclude superseded/ and archive/ ────────────────────────────
        if is_excluded_path(path):
            print(f"[SKIP:excluded] {path}")
            metrics["docs_skipped_excluded"] += 1
            continue
        # Migration hint for deprecated-bucket docs (non-blocking)
        if is_deprecated_bucket(path):
            cat = get_category(path)
            target = {
                "runbooks":"reference","sops":"reference","guides":"reference",
                "playbooks":"reference","rollback_refs":"reference",
                "fleet":"reference","compliance":"reference","from-macmini":"reference",
                "council":"decisions",
                "reviews":"record","research":"record",
                "strategies":"plans","proposals":"plans",
            }.get(cat, "reference")
            append_remediation_backlog({
                "type": "deprecated_bucket", "path": path,
                "current_bucket": cat, "target_bucket": target,
                "action": "move per DOC_ONTOLOGY_CANONICAL.md v1.2", "resolved": False,
            })

        # ── Gate 1: Frontmatter index flag ──────────────────────────────────────
        indexable, reason = should_index_file(path)
        if not indexable:
            print(f"[SKIP:index-gate] {path} — {reason}")
            metrics["docs_skipped_index_gate"] += 1
            continue

        # ── (e2) Taxonomy enforcement ───────────────────────────────────────────
        if not is_taxonomy_valid(path):
            cat = get_category(path)
            msg = f"Taxonomy violation: {path} (category '{cat}' not in approved list)"
            print(f"[WARN] {msg}", file=sys.stderr)
            taxonomy_violations.append({"path": path, "category": cat})
            append_remediation_backlog({
                "type": "taxonomy_violation",
                "path": path,
                "category": cat,
                "resolved": False,
            })
            continue

        # Checksum check
        current_hash = file_checksum(path)
        stored_hash = get_db_stored_hash(conn, path)

        if path not in embedded_paths or (stored_hash and stored_hash != current_hash):
            files_to_embed.append(path)

        # ── (e) Staleness check ─────────────────────────────────────────────────
        cat = get_category(path)
        if check_staleness(path, cat):
            stale_docs.append({"path": path, "category": cat})

    metrics["taxonomy_violations"] = taxonomy_violations
    if taxonomy_violations:
        fleet_alert(
            f"⚠️ KB taxonomy violations found: {len(taxonomy_violations)} docs in unapproved folders. "
            f"Check {REMEDIATION_BACKLOG_PATH}",
            dry_run=dry_run,
        )

    metrics["docs_stale"] = stale_docs
    if len(stale_docs) > 5:
        stale_list = ", ".join(os.path.basename(d["path"]) for d in stale_docs[:5])
        fleet_alert(
            f"⚠️ KB staleness alert: {len(stale_docs)} stale docs (>{5} threshold). "
            f"Examples: {stale_list}",
            dry_run=dry_run,
        )

    # Cost estimate
    total_chars = sum(
        os.path.getsize(p) for p in files_to_embed if os.path.exists(p)
    )
    cost_estimate = total_chars * COST_PER_CHAR
    metrics["cost_estimate_usd"] = round(cost_estimate, 6)

    if cost_estimate > COST_CEIL_USD:
        msg = (
            f"🚨 KB embed cost ceiling hit: estimated ${cost_estimate:.4f} "
            f"(ceiling: ${COST_CEIL_USD}). Aborting embed run. Review files_to_embed manually."
        )
        print(f"[ABORT] {msg}", file=sys.stderr)
        fleet_alert(msg, dry_run=dry_run)
        # Write what we have so far
        with open(LAST_RUN_PATH, "w") as f:
            json.dump(metrics, f, indent=2, default=str)
        conn.close()
        sys.exit(1)

    # ── (b) Embed new/changed files ────────────────────────────────────────────
    retry_paths = {e["path"] for e in failures if "path" in e and should_retry(e)}

    for path in files_to_embed:
        # ── Gate 3: Semantic deduplication ─────────────────────────────────────
        is_dup, dup_hash = check_semantic_duplicate(path, conn)
        if is_dup:
            print(f"[SKIP:dedup] {path} matches existing hash {dup_hash[:16]}…")
            metrics["docs_skipped_dedup"] += 1
            append_remediation_backlog({
                "type": "potential_duplicate",
                "path": path,
                "matching_hash": dup_hash,
                "resolved": False,
            })
            # Dedup is routine — don't fleet alert. Only log to metrics + backlog.
            continue

        print(f"[EMBED] {path}")
        ok = embed_file(path, dry_run=dry_run, conn=conn)
        if ok:
            metrics["embed_successes"] += 1
            metrics["docs_added"] += 1
            # Mark resolved in failures if was failing
            for entry in failures:
                if entry.get("path") == path:
                    entry["resolved"] = True
        else:
            metrics["embed_failures"] += 1
            failures = log_embed_failure(path, "memory capture returned non-zero", failures)
            append_remediation_backlog({
                "type": "embed_failure",
                "path": path,
                "resolved": False,
            })

    # Retry previously failed files
    for path in retry_paths:
        if path not in files_to_embed and os.path.exists(path) and is_taxonomy_valid(path) \
                and not is_excluded_path(path) and should_index_file(path)[0]:
            print(f"[RETRY] {path}")
            ok = embed_file(path, dry_run=dry_run, conn=conn)
            if ok:
                metrics["embed_successes"] += 1
                metrics["docs_added"] += 1
                for entry in failures:
                    if entry.get("path") == path:
                        entry["resolved"] = True
            else:
                metrics["embed_failures"] += 1
                failures = log_embed_failure(path, "retry failed", failures)

    # Persist failures
    save_embed_failures(failures)

    if metrics["embed_failures"] > 0:
        fleet_alert(
            f"⚠️ KB embed failures: {metrics['embed_failures']} files failed to embed. "
            f"Check {EMBED_FAILURES_PATH}",
            dry_run=dry_run,
        )

    # ── (d) Generate KNOWLEDGE_INDEX.md from pgvector ─────────────────────────
    # Re-query after embeds
    doc_chunks = get_all_doc_chunks(conn)
    index_count = generate_index(doc_chunks, dry_run=dry_run)
    metrics["index_entries_generated"] = index_count

    # ── (f) Gap detection ──────────────────────────────────────────────────────
    gaps = detect_gaps()
    metrics["gaps"] = gaps
    if gaps:
        gap_names = ", ".join(g["plan_name"][:40] for g in gaps[:3])
        fleet_alert(
            f"⚠️ KB gap detected: {len(gaps)} completed cascade plans missing reference artifacts. "
            f"Plans: {gap_names}",
            dry_run=dry_run,
        )

    # ── (g) Write last_run.json ────────────────────────────────────────────────
    metrics["timestamp"] = datetime.now(timezone.utc).isoformat()
    with open(LAST_RUN_PATH, "w") as f:
        json.dump(metrics, f, indent=2, default=str)

    conn.close()

    print(
        f"[DONE] scanned={metrics['docs_scanned']} added={metrics['docs_added']} "
        f"pruned={metrics['docs_pruned']} stale={len(metrics['docs_stale'])} "
        f"index_entries={index_count} failures={metrics['embed_failures']} "
        f"skipped_excluded={metrics['docs_skipped_excluded']} "
        f"skipped_index_gate={metrics['docs_skipped_index_gate']} "
        f"skipped_dedup={metrics['docs_skipped_dedup']} "
        f"cost=${metrics['cost_estimate_usd']:.6f}"
    )
    return metrics


# ── Health Check ───────────────────────────────────────────────────────────────
def health_check():
    """Return JSON health status to stdout."""
    result = {}

    # last_run.json
    if os.path.exists(LAST_RUN_PATH):
        try:
            with open(LAST_RUN_PATH) as f:
                last_run = json.load(f)
            result.update(last_run)
            # Compute seconds since last run
            ts = last_run.get("timestamp")
            if ts:
                try:
                    last_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                    result["seconds_since_last_run"] = int(
                        (datetime.now(timezone.utc) - last_dt).total_seconds()
                    )
                except Exception:
                    result["seconds_since_last_run"] = -1
        except Exception as e:
            result["last_run_error"] = str(e)
            result["seconds_since_last_run"] = -1
    else:
        result["seconds_since_last_run"] = -1
        result["last_run_note"] = "No last_run.json found — script has not been run yet."

    # Open backlog count
    result["open_backlog_count"] = count_open_backlog()

    # Last dedup date
    dedup_report = "/opt/gcg/shared/logs/dedup_report.json"
    if os.path.exists(dedup_report):
        try:
            with open(dedup_report) as f:
                d = json.load(f)
            result["last_dedup_date"] = d.get("scan_date", "unknown")
        except Exception:
            result["last_dedup_date"] = "error_reading"
    else:
        result["last_dedup_date"] = "never"

    print(json.dumps(result, indent=2, default=str))


# ── CLI ────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="GCG Knowledge Base Maintenance")
    parser.add_argument("--dry-run", action="store_true", help="Scan only, no writes")
    parser.add_argument("--health", action="store_true", help="Print JSON health status")
    args = parser.parse_args()

    if args.health:
        health_check()
        return

    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
