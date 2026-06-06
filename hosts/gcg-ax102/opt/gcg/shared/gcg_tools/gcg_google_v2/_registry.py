"""
_registry.py — Single action registry: action → {scope_key, risk_class}.

Replaces hand-passed scope_key and risk_class across every call site.
Adding a new operation = ONE registry line + one thin wrapper function.

Callers never see scope_key or risk. The registry derives both.
Risk is validated against the LIVE DB CHECK constraint at call time,
not against a stale code constant — so if Peter adds 'critical' tomorrow,
it works without a code deploy.

Design:
  ACTION_REGISTRY dict is the sole source of truth for scope+risk.
  lookup_action() returns (scope_key, risk_class).
  validate_risk_class() queries the live DB constraint once and caches.
  No function in drive.py, gmail.py, etc. hand-passes either value.
"""
import logging
from typing import Optional

log = logging.getLogger(__name__)

# ── Single source of truth ──────────────────────────────────────────────────
# Structure: action -> {"scope": scope_key, "risk": risk_class}
# scope_key matches _SCOPES_BY_SERVICE in _services.py
# risk_class must match google_audit_log CHECK constraint (read/medium/high)

ACTION_REGISTRY: dict[str, dict[str, str]] = {
    # ── Gmail ────────────────────────────────────────────────────────────────
    "gmail.messages.list":        {"scope": "gmail-read",    "risk": "read"},
    "gmail.messages.get":         {"scope": "gmail-read",    "risk": "read"},
    "gmail.messages.send":        {"scope": "gmail-send",    "risk": "high"},
    "gmail.messages.modify":      {"scope": "gmail-modify",  "risk": "medium"},
    "gmail.messages.delete":      {"scope": "gmail-modify",  "risk": "high"},
    "gmail.messages.trash":       {"scope": "gmail-modify",  "risk": "high"},
    "gmail.messages.batchModify": {"scope": "gmail-modify",  "risk": "medium"},
    "gmail.threads.list":         {"scope": "gmail-read",    "risk": "read"},
    "gmail.threads.get":          {"scope": "gmail-read",    "risk": "read"},
    "gmail.labels.list":          {"scope": "gmail-read",    "risk": "read"},
    "gmail.drafts.create":        {"scope": "gmail-compose", "risk": "medium"},
    "gmail.attachments.get":      {"scope": "gmail-read",    "risk": "read"},

    # ── Drive ────────────────────────────────────────────────────────────────
    "drive.files.list":           {"scope": "drive-read",   "risk": "read"},
    "drive.files.get":            {"scope": "drive-read",   "risk": "read"},
    "drive.files.get_content":    {"scope": "drive-read",   "risk": "read"},
    "drive.files.export":         {"scope": "drive-read",   "risk": "read"},
    "drive.files.get_media":      {"scope": "drive-read",   "risk": "read"},
    "drive.files.create":         {"scope": "drive-write",  "risk": "medium"},
    "drive.files.copy":           {"scope": "drive-write",  "risk": "medium"},
    "drive.files.update":         {"scope": "drive-write",  "risk": "medium"},
    "drive.files.move":           {"scope": "drive-write",  "risk": "medium"},
    "drive.files.trash":          {"scope": "drive-write",  "risk": "medium"},
    "drive.files.delete":         {"scope": "drive-write",  "risk": "high"},
    "drive.folders.create":       {"scope": "drive-write",  "risk": "medium"},
    "drive.permissions.create":   {"scope": "drive-write",  "risk": "high"},

    # ── Calendar ─────────────────────────────────────────────────────────────
    "calendar.events.list":       {"scope": "calendar-read",  "risk": "read"},
    "calendar.events.get":        {"scope": "calendar-read",  "risk": "read"},
    "calendar.events.insert":     {"scope": "calendar-write", "risk": "medium"},
    "calendar.events.update":     {"scope": "calendar-write", "risk": "medium"},
    "calendar.events.delete":     {"scope": "calendar-write", "risk": "medium"},

    # ── Docs ─────────────────────────────────────────────────────────────────
    "docs.documents.get":         {"scope": "docs-read",   "risk": "read"},
    "docs.documents.create":      {"scope": "docs-write",  "risk": "medium"},
    "docs.documents.batchUpdate": {"scope": "docs-write",  "risk": "medium"},

    # ── Sheets ───────────────────────────────────────────────────────────────
    "sheets.values.get":                 {"scope": "sheets-read",  "risk": "read"},
    "sheets.values.update":              {"scope": "sheets-write", "risk": "medium"},
    "sheets.spreadsheets.create":        {"scope": "sheets-write", "risk": "medium"},
    "sheets.spreadsheets.values.append": {"scope": "sheets-write", "risk": "medium"},
    "sheets.spreadsheets.values.clear":  {"scope": "sheets-write", "risk": "medium"},

    # ── Slides ───────────────────────────────────────────────────────────────
    "slides.presentations.get":    {"scope": "slides-read",  "risk": "read"},

    # ── Contacts / People API ──────────────────────────────────────────────────
    "contacts.list":       {"scope": "contacts-read",   "risk": "read"},
    "contacts.get":        {"scope": "contacts-read",   "risk": "read"},
    "contacts.search":     {"scope": "contacts-read",   "risk": "read"},
    "contacts.create":     {"scope": "contacts-write",  "risk": "medium"},
    "contacts.update":     {"scope": "contacts-write",  "risk": "medium"},
    "contacts.delete":     {"scope": "contacts-write",  "risk": "high"},

    # ── Admin Directory ──────────────────────────────────────────────────────
    "admin.users.list":            {"scope": "admin-read",  "risk": "read"},
    "admin.users.get":             {"scope": "admin-read",  "risk": "read"},
    "admin.users.aliases.list":    {"scope": "admin-read",  "risk": "read"},
    "admin.groups.list":           {"scope": "admin-group",  "risk": "read"},
    "admin.groups.get":            {"scope": "admin-group",  "risk": "read"},

    # ── Tag Manager ──────────────────────────────────────────────────────────
    "tagmanager.accounts.list":    {"scope": "tagmanager-read",    "risk": "read"},
    "tagmanager.containers.list":  {"scope": "tagmanager-read",    "risk": "read"},
    "tagmanager.containers.get":   {"scope": "tagmanager-read",    "risk": "read"},
    "tagmanager.workspaces.list":  {"scope": "tagmanager-read",    "risk": "read"},
    "tagmanager.tags.list":        {"scope": "tagmanager-read",    "risk": "read"},
    "tagmanager.tags.create":      {"scope": "tagmanager-edit",    "risk": "medium"},
    "tagmanager.tags.update":      {"scope": "tagmanager-edit",    "risk": "medium"},
    "tagmanager.tags.delete":      {"scope": "tagmanager-edit",    "risk": "medium"},
    "tagmanager.triggers.list":    {"scope": "tagmanager-read",    "risk": "read"},
    "tagmanager.variables.list":   {"scope": "tagmanager-read",    "risk": "read"},
    "tagmanager.versions.create":  {"scope": "tagmanager-publish", "risk": "high"},
    "tagmanager.versions.publish": {"scope": "tagmanager-publish", "risk": "high"},
}

# ── Live DB constraint cache ─────────────────────────────────────────────────
_ALLOWED_RISK_CLASSES: Optional[set[str]] = None


def _load_allowed_risk_classes(conn) -> set[str]:
    """
    Query the live DB CHECK constraint for google_audit_log.risk_class.
    This is the SINGLE source of truth for valid risk values.
    If we can't query (DB down), fall back to known set — fail-closed.
    """
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT pg_get_constraintdef(oid)
                FROM pg_constraint
                WHERE conname = 'google_audit_log_risk_class_check'
            """)
            row = cur.fetchone()
            if row:
                raw = row[0]
                # Parse: CHECK (((risk_class)::text = ANY (ARRAY[('read'::character varying)::text, ...])))
                import re
                matches = re.findall(r"'([^']+)'::character varying", raw)
                if matches:
                    return set(matches)
    except Exception as e:
        log.warning("Could not query risk_class constraint: %s", e)
    # Fallback: hardcoded set matching current DB constraint
    return {"read", "medium", "high"}


def validate_risk_class(conn, risk_class: str) -> None:
    """
    Validate risk_class against the LIVE DB CHECK constraint.
    Raises ValueError with a clear message naming the bad value.
    Never a cryptic DB error — fails fast at the call site.
    """
    global _ALLOWED_RISK_CLASSES
    if _ALLOWED_RISK_CLASSES is None:
        _ALLOWED_RISK_CLASSES = _load_allowed_risk_classes(conn)

    if risk_class not in _ALLOWED_RISK_CLASSES:
        raise ValueError(
            f"Invalid risk_class={risk_class!r}. "
            f"Must be one of {sorted(_ALLOWED_RISK_CLASSES)}. "
            f"Action registry entry or DB constraint needs updating."
        )


def lookup_action(action: str) -> dict:
    """
    Look up registry entry for action.
    
    Returns:
        {"scope": scope_key, "risk": risk_class}
    
    Raises:
        KeyError: action not in registry (fail-fast at dev time)
    """
    if action not in ACTION_REGISTRY:
        raise KeyError(
            f"Unknown action={action!r}. "
            f"Add to ACTION_REGISTRY in _registry.py before using. "
            f"Available: {sorted(ACTION_REGISTRY.keys())}"
        )
    return ACTION_REGISTRY[action]


def clear_risk_cache() -> None:
    """Force re-query on next call (e.g. after DB migration)."""
    global _ALLOWED_RISK_CLASSES
    _ALLOWED_RISK_CLASSES = None
