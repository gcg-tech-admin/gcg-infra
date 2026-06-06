"""
mcp_tools.py — MCP tool factory for gcg_google_v2.

Iterates ACTION_REGISTRY, resolves each action to its Python function,
wraps it in an MCP tool with a proper inputSchema.

Uses exec() to generate wrapper functions with correct parameter names
so FastMCP derives proper input schemas. Type annotations are excluded
from generated code to avoid exec namespace pollution; FastMCP still
produces valid schemas from param defaults.
"""
import inspect
import logging
import typing
from typing import Optional

from mcp.server.fastmcp import FastMCP

from . import _auth, _audit, _registry, gmail, drive, calendar, docs, sheets, slides, tagmanager, admin, contacts
from .mcp_tool_schema import service_action_to_mcp_name, build_input_schema

log = logging.getLogger(__name__)

_ACTION_FN_MAP: dict[str, str] = {
    "gmail.messages.list":        "gmail.list_messages",
    "gmail.messages.get":         "gmail.get_message",
    "gmail.messages.send":        "gmail.send_message",
    "gmail.messages.modify":      "gmail.modify_message",
    "gmail.messages.delete":      "gmail.delete_message",
    "gmail.messages.trash":       "gmail.trash_message",
    "gmail.messages.batchModify": "gmail.batch_modify_messages",
    "gmail.threads.list":         "gmail.list_threads",
    "gmail.threads.get":          "gmail.get_thread",
    "gmail.labels.list":          "gmail.list_labels",
    "gmail.drafts.create":        "gmail.create_draft",
    "gmail.attachments.get":      "gmail.get_attachment",
    "drive.files.list":            "drive.list_files",
    "drive.files.get":             "drive.get_file_metadata",
    "drive.files.get_content":     "drive.download_file",
    "drive.files.export":          "drive.export_file",
    "drive.files.get_media":       "drive.get_file_media",
    "drive.files.create":          "drive.create_file",
    "drive.files.copy":            "drive.copy_file",
    "drive.files.update":          "drive.update_file",
    "drive.files.move":            "drive.move_file",
    "drive.files.trash":           "drive.trash_file",
    "drive.files.delete":          "drive.delete_file",
    "drive.folders.create":        "drive.create_folder",
    "drive.permissions.create":    "drive.create_permission",
    "calendar.events.list":        "calendar.list_events",
    "calendar.events.get":         "calendar.get_event",
    "calendar.events.insert":      "calendar.create_event",
    "calendar.events.update":      "calendar.update_event",
    "calendar.events.delete":      "calendar.delete_event",
    "docs.documents.get":          "docs.get_document",
    "docs.documents.create":       "docs.create_document",
    "docs.documents.batchUpdate":  "docs.batch_update",
    "sheets.values.get":                 "sheets.get_values",
    "sheets.values.update":              "sheets.update_values",
    "sheets.spreadsheets.values.append": "sheets.append_values",
    "sheets.spreadsheets.values.clear":  "sheets.clear_values",
    "sheets.spreadsheets.create":        "sheets.create_spreadsheet",
    "slides.presentations.get":          "slides.get_presentation",
    "admin.users.list":           "admin.list_users",
    "admin.users.get":            "admin.get_user",
    "admin.users.aliases.list":   "admin.list_aliases",
    "admin.groups.list":          "admin.list_groups",
    "admin.groups.get":           "admin.get_group",
    "contacts.list":       "contacts.list_contacts",
    "contacts.get":        "contacts.get_contact",
    "contacts.create":     "contacts.create_contact",
    "contacts.update":     "contacts.update_contact",
    "contacts.delete":     "contacts.delete_contact",
    "contacts.search":     "contacts.search_contacts",
    "tagmanager.accounts.list":    "tagmanager.list_accounts",
    "tagmanager.containers.list":  "tagmanager.list_containers",
    "tagmanager.containers.get":   "tagmanager.get_container",
    "tagmanager.workspaces.list":  "tagmanager.list_workspaces",
    "tagmanager.tags.list":        "tagmanager.list_tags",
    "tagmanager.tags.create":      "tagmanager.create_tag",
    "tagmanager.tags.update":      "tagmanager.update_tag",
    "tagmanager.tags.delete":      "tagmanager.delete_tag",
    "tagmanager.triggers.list":    "tagmanager.list_triggers",
    "tagmanager.variables.list":   "tagmanager.list_variables",
    "tagmanager.versions.create":  "tagmanager.create_version",
    "tagmanager.versions.publish": "tagmanager.publish_version",
}

_SERVICE_MODULES = {"gmail": gmail, "drive": drive, "calendar": calendar,
                    "docs": docs, "sheets": sheets, "slides": slides,
                    "tagmanager": tagmanager, "admin": admin,
                    "contacts": contacts}

_FUNCTION_CACHE: dict = {}


def _resolve_function(action: str):
    if action in _FUNCTION_CACHE:
        return _FUNCTION_CACHE[action]
    fn_ref = _ACTION_FN_MAP.get(action)
    if not fn_ref:
        raise ValueError(f"No function mapping for action={action} — not yet implemented")
    parts = fn_ref.split(".")
    mod = _SERVICE_MODULES.get(parts[0])
    if not mod:
        raise ValueError(f"Unknown module: {parts[0]}")
    fn = getattr(mod, parts[1], None)
    if fn is None or not callable(fn):
        raise ValueError(f"Function {fn_ref} not found in {parts[0]}")
    _FUNCTION_CACHE[action] = fn
    return fn


def _generate_description(action: str, entry: dict) -> str:
    s = action.split(".", 1)[0]
    r = entry.get("risk", "read")
    lbl = {"read": "READ-ONLY", "medium": "MODIFY", "high": "HIGH-RISK"}.get(r, r.upper())
    return f"[{lbl}] {s}: {action}"


def _execute_tool(conn, agent_id: str, action: str, **kwargs):
    impersonate_user = kwargs.pop("impersonate_user", None)
    if not impersonate_user:
        raise ValueError("impersonate_user is required")
    _auth.check_impersonation(agent_id, impersonate_user, conn)
    approval_token = kwargs.pop("approval_token", None)
    entry = _registry.lookup_action(action)
    audit_params = {k: v for k, v in kwargs.items() if isinstance(v, (str, int, float, bool))}
    audit_id = _audit.write_registry_entry(conn, agent_id, action, impersonate_user, audit_params,
                                           approval_token_id=approval_token)
    fn = _resolve_function(action)
    try:
        sig = inspect.signature(fn)
        if "approval_token" in sig.parameters and approval_token is not None:
            result = fn(conn, agent_id, impersonate_user, **kwargs, approval_token=approval_token)
        else:
            result = fn(conn, agent_id, impersonate_user, **kwargs)
        _audit.resolve_audit(conn, audit_id, "success", "ok", None)
        return result if isinstance(result, dict) else {"success": True}
    except Exception as exc:
        _audit.resolve_audit(conn, audit_id, "failed", None, str(exc)[:500])
        raise


def _build_input_schema_no_exec(fn) -> dict:
    """Build input schema dict, similar to build_input_schema but for generated fn."""
    # Use build_input_schema from the tool_schema module
    return build_input_schema(fn)


def register_all_tools(mcp: FastMCP, conn, agent_id: str, sa_info: dict) -> int:
    registered = 0
    _globals = {"_execute_tool": _execute_tool, "_conn": conn, "_agent_id": agent_id}

    for action in _registry.ACTION_REGISTRY:
        try:
            fn = _resolve_function(action)
        except (ValueError, KeyError):
            continue

        mcp_name = service_action_to_mcp_name(action)
        entry = _registry.ACTION_REGISTRY[action]
        description = _generate_description(action, entry)

        # Build params from function signature WITHOUT type annotations to avoid exec namespace issues
        sig = inspect.signature(fn)
        params = []
        names = []
        for pname, param in sig.parameters.items():
            if pname in ("conn", "agent_id"):
                continue
            d = param.default
            if param.default is inspect.Parameter.empty:
                params.append(pname)
            elif d is None:
                params.append(f"{pname}=None")
            elif isinstance(d, str):
                params.append(f'{pname}={d!r}')
            elif isinstance(d, bool):
                params.append(f"{pname}={str(d)}")
            else:
                params.append(f"{pname}={d}")
            names.append(pname)

        ps = ", ".join(params)
        kvs = ", ".join(f"{n}={n}" for n in names)
        fname = f"_tw_{registered}"
        code = f"def {fname}({ps}):\n    return _execute_tool(_conn, _agent_id, {action!r}, {kvs})\n"

        try:
            exec(code, _globals)
            tool_fn = _globals[fname]
            # Set input schema from the original function for FastMCP to use
            tool_fn._input_schema = build_input_schema(fn)
            # Also set as __signature__ override
            from inspect import Signature, Parameter
            new_params = []
            for pname, param in sig.parameters.items():
                if pname in ("conn", "agent_id"):
                    continue
                new_params.append(Parameter(pname, kind=Parameter.KEYWORD_ONLY,
                                            default=param.default))
            tool_fn.__signature__ = Signature(parameters=new_params)

            mcp.tool(name=mcp_name, description=description)(tool_fn)
            registered += 1
        except Exception as e:
            log.warning("Failed to register %s: %s", action, e)

    return registered
