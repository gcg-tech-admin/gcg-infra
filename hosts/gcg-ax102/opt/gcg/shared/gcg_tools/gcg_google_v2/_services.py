"""
_services.py \u2014 Google SDK service builder with caching and per-action scopes.

Uses the EXACT scope set authorized in the DWD service account config
(Google Admin \u2192 Security \u2192 API Controls \u2192 Domain-wide Delegation).
Scopes must match what the old broker requests; any drift = "unauthorized_client".

IMPORTANT: Google Docs uses DRIVE scopes internally (drive.readonly / drive).
Google Sheets uses SPREADSHEETS scope. Only calendar.readonly is NOT authorized,
so calendar reads use the full calendar scope.
"""
import logging
from typing import Optional

from . import _registry

log = logging.getLogger(__name__)

_SERVICE_CACHE: dict = {}

# Scope sets per service key \u2014 matches broker scopes_for_action authorized in DWD
_SCOPES_BY_SERVICE = {
    # Gmail \u2014 specific scopes authorized
    "gmail-read":     ["https://www.googleapis.com/auth/gmail.readonly"],
    "gmail-modify":   ["https://www.googleapis.com/auth/gmail.modify"],
    "gmail-compose":  ["https://www.googleapis.com/auth/gmail.compose"],
    "gmail-send":     ["https://www.googleapis.com/auth/gmail.send"],

    # Drive \u2014 both readonly and full are authorized
    "drive-read":     ["https://www.googleapis.com/auth/drive.readonly"],
    "drive-write":    ["https://www.googleapis.com/auth/drive"],

    # Calendar \u2014 ONLY full scope authorized (no calendar.readonly in DWD)
    "calendar-read":  ["https://www.googleapis.com/auth/calendar"],
    "calendar-write": ["https://www.googleapis.com/auth/calendar"],

    # Docs \u2014 read via drive.readonly; write requires documents scope
    "docs-read":      ["https://www.googleapis.com/auth/drive.readonly"],
    "docs-write":     ["https://www.googleapis.com/auth/documents", "https://www.googleapis.com/auth/drive"],

    # Sheets \u2014 only full spreadsheets scope authorized
    "sheets-read":    ["https://www.googleapis.com/auth/spreadsheets"],
    "sheets-write":   ["https://www.googleapis.com/auth/spreadsheets"],

    # Slides \u2014 both readonly and full are authorized
    "slides-read":    ["https://www.googleapis.com/auth/presentations.readonly"],
    "slides-write":   ["https://www.googleapis.com/auth/presentations"],

    # Tag Manager — scopes must be added to DWD in Google Admin
    # (Security → API Controls → Domain-wide Delegation → service account)
    "tagmanager-read":    ["https://www.googleapis.com/auth/tagmanager.readonly"],
    "tagmanager-edit":    ["https://www.googleapis.com/auth/tagmanager.edit.containers"],
    "tagmanager-publish": ["https://www.googleapis.com/auth/tagmanager.publish"],

    # People API (Contacts)
    "contacts-read":    ["https://www.googleapis.com/auth/contacts.readonly"],
    "contacts-write":   ["https://www.googleapis.com/auth/contacts"],

    # Admin Directory
    "admin-read":     ["https://www.googleapis.com/auth/admin.directory.user.readonly"],
    "admin-write":    ["https://www.googleapis.com/auth/admin.directory.user"],
    "admin-group":    ["https://www.googleapis.com/auth/admin.directory.group"],
}


def get_service_for_action(
    sa_info: dict,
    service_name: str,
    version: str,
    subject: str,
    action: str,
):
    """
    Build and cache a service object using scope_key derived from the action registry.
    
    Callers pass the action name (e.g. "drive.files.list"). The registry
    maps action -> scope_key -> scope URLs. No caller ever passes a scope explicitly.
    """
    entry = _registry.lookup_action(action)
    return get_service(sa_info, service_name, version, subject, scope_key=entry["scope"])


def get_service(
    sa_info: dict,
    service_name: str,
    version: str,
    subject: str,
    scope_key: str = None,
    scopes: Optional[list] = None,
):
    """
    Build and cache a Google API service object with specific scopes.

    Args:
        sa_info: Service account JSON dict
        service_name: e.g. "gmail", "drive", "calendar", "docs", "sheets", "slides"
        version: e.g. "v1", "v3"
        subject: Email to impersonate via DWD
        scope_key: Key into _SCOPES_BY_SERVICE (e.g. "gmail-read")
        scopes: Override scopes directly (takes precedence over scope_key)

    Returns:
        googleapiclient.discovery.Resource
    """
    import google.oauth2.service_account as sa_module
    import googleapiclient.discovery as discovery

    if scopes is None:
        if scope_key and scope_key in _SCOPES_BY_SERVICE:
            scopes = _SCOPES_BY_SERVICE[scope_key]
        else:
            # Fallback: {service}-read
            scopes = _SCOPES_BY_SERVICE.get(f"{service_name}-read", [])

    cache_key = (service_name, version, subject, tuple(scopes))
    if cache_key in _SERVICE_CACHE:
        return _SERVICE_CACHE[cache_key]

    credentials = sa_module.Credentials.from_service_account_info(
        sa_info,
        scopes=list(scopes),
        subject=subject,
    )
    service = discovery.build(service_name, version, credentials=credentials, cache_discovery=False)
    _SERVICE_CACHE[cache_key] = service
    return service


def clear_cache() -> None:
    """Clear service cache (e.g. after credential rotation)."""
    _SERVICE_CACHE.clear()
