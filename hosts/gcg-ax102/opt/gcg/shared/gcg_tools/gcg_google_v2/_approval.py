"""
_approval.py — Approval gate enforcement.

Mirrors broker logic:
1. Check agent_trusted_actions for standing trust override
2. If trusted: bypass approval token (log as TRUSTED)
3. If not trusted: require valid approval_token from approval_tokens table
4. Consume token (mark used_at) after validation

Also provides PolicyViolationError for blocking direct google.oauth2/googleapiclient
imports outside the library.

Risk classes match broker RISK_MATRIX:
  read   — no approval needed (list/get operations)
  medium — no approval needed (drafts, creates, modifies)
  high   — requires trust or approval token (send, delete, external share)
"""
import logging
from typing import Optional

log = logging.getLogger(__name__)


class PolicyViolationError(Exception):
    """Raised when an operation violates GCG Google access policy."""


# High-risk actions — must match broker RISK_MATRIX exactly for parity
_HIGH_RISK_ACTIONS: set[str] = {
    # Service-prefixed names (canonical — all callers use these)
    "gmail.drafts.send",
    "gmail.messages.send",
    "gmail.messages.delete",
    "gmail.messages.trash",
    "drive.files.delete",
    "drive.permissions.create",
    "drive.files.permissions.create",
    "drive.files.permissions.delete",
    "drive.files.share_external",
    "calendar.events.insert_with_external_attendees",
    "sheets.spreadsheets.values.batchUpdate_bulk",
    "tagmanager.versions.create",
    "tagmanager.versions.publish",
    "contacts.delete",
    # Unprefixed short names (legacy backward compat)
    "drafts.send",
    "messages.send",
    "messages.delete",
    "messages.trash",
    "files.delete",
    "files.permissions.create",
    "files.permissions.delete",
    "files.share_external",
    "events.insert_with_external_attendees",
    "spreadsheets.values.batchUpdate_bulk",
    "tagmanager.publish",
    "tagmanager.delete_version",
}

# Medium-risk — no approval needed but audit-tagged
_MEDIUM_RISK_ACTIONS: set[str] = {
    "drafts.create", "drafts.update",
    "files.create", "files.upload", "files.convert", "files.copy",
    "files.update", "files.patch",
    "files.permissions.update",
    "files.comments.create",
    "messages.modify", "messages.batchModify",
    "labels.create", "labels.update", "labels.delete", "labels.modify",
    "filters.create", "filters.delete",
    "events.insert", "events.update", "events.patch", "events.delete",
    "documents.create", "documents.batchUpdate",
    "spreadsheets.create", "spreadsheets.batchUpdate",
    "spreadsheets.values.update", "spreadsheets.values.batchUpdate",
    "tagmanager.create_version", "tagmanager.update_workspace", "tagmanager.create_workspace",
    "spreadsheets.values.append", "spreadsheets.values.clear",
    "presentations.create", "presentations.batchUpdate",
    "tasks.create", "tasks.update", "tasks.delete",
    "ads.campaigns.update", "ads.adgroups.update",
    "google_ads_write",
    "business.reviews.reply", "business.posts.create",
    "business.locations.update", "business.media.create",
    "google_business_write",
    "youtube_write",
    "videos.update",
    "aliases.insert", "aliases.delete",
}


def classify_risk(action: str, action_registry: Optional[dict] = None) -> str:
    """
    Return 'read', 'medium', or 'high' for the given action.

    Uses ACTION_REGISTRY (canonical — prefixed names like gmail.messages.send)
    when available, with hardcoded sets as fallback for backward compat.
    """
    # Primary: ACTION_REGISTRY — handles prefixed names (gmail.messages.send)
    if action_registry is not None:
        entry = action_registry.get(action)
        if entry is not None:
            return entry.get("risk", "read")
    else:
        try:
            from ._registry import ACTION_REGISTRY
            entry = ACTION_REGISTRY.get(action)
            if entry is not None:
                return entry.get("risk", "read")
        except ImportError:
            pass

    # Fallback: check hardcoded sets (short-name backward compat)
    if action in _HIGH_RISK_ACTIONS:
        return "high"
    if action in _MEDIUM_RISK_ACTIONS:
        return "medium"
    return "read"


def _is_trusted(conn, agent_id: str, action: str) -> bool:
    """Check agent_trusted_actions for a standing trust override."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM agent_trusted_actions "
                "WHERE agent_id = %s AND action = %s AND revoked_at IS NULL",
                (agent_id, action)
            )
            return cur.fetchone() is not None
    except Exception as e:
        log.warning("Trust check failed for %s/%s: %s", agent_id, action, e)
        return False


def _consume_approval_token(conn, token_id: str, agent_id: str, action: str) -> bool:
    """
    Consume a single-use approval token. Returns True if valid and consumed.
    Matches actual approval_tokens schema: id, issued_for, action, expires_at, used_at, revoked_at.
    """
    import uuid
    try:
        uuid.UUID(token_id)
    except (ValueError, AttributeError):
        return False

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE approval_tokens
            SET used_at = NOW()
            WHERE id = %s
              AND used_at IS NULL
              AND revoked_at IS NULL
              AND expires_at > NOW()
              AND action = %s
              AND (issued_for = %s OR issued_for = 'any')
            """,
            (token_id, action, agent_id)
        )
        consumed = cur.rowcount == 1
        conn.commit()
        return consumed


def check_approval(
    conn,
    agent_id: str,
    action: str,
    approval_token: Optional[str] = None,
) -> str:
    """
    Verify the agent has approval for the given action.

    Returns "trusted" if bypassed via agent_trusted_actions,
    "consumed" if an approval token was validated,
    or "none" if no approval needed (read/medium risk).

    Raises PolicyViolationError if approval required but not provided/valid.
    """
    risk = classify_risk(action)

    if risk != "high":
        return "none"

    # Trust bypass — matches broker logic
    if _is_trusted(conn, agent_id, action):
        log.info("TRUSTED: agent=%s action=%s (approval_token bypassed)", agent_id, action)
        return "trusted"

    if not approval_token:
        raise PolicyViolationError(
            f"Action '{action}' is high-risk and requires an approval token. "
            f"Ask Peter to issue one, or request trust via: "
            f"INSERT INTO agent_trusted_actions (agent_id, action) VALUES ('{agent_id}', '{action}')"
        )

    if not _consume_approval_token(conn, approval_token, agent_id, action):
        raise PolicyViolationError(
            f"Approval token invalid, expired, already-used, or does not match "
            f"(agent={agent_id}, action={action})."
        )
    return "consumed"
