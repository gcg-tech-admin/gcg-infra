#!/usr/bin/env python3
"""fleet-aggregate — extract live fleet state.

Reads each agent's openclaw.json + manifest.json + systemd MemoryMax,
emits a JSON record per agent. Source of truth for fleet-yaml-gen.

Output: /opt/gcg/shared/state/fleet-records.json (persistent path; replaces /tmp)
"""
import json, os, glob, hashlib, re, subprocess
from pathlib import Path

OUT = "/opt/gcg/shared/state/fleet-records.json"
CRITICAL = {"daen", "talos", "marcus", "mnemosyne", "vulcan", "nik"}
TIER_A   = {"bob", "leon", "hector", "tom", "phil", "algaib"}
TIER_C   = {"talos", "vulcan"}
TIER_D   = {"daen", "niccolo"}


def systemctl_memorymax(agent):
    """Query systemctl directly — MemoryMax lives in main service unit, not drop-ins."""
    try:
        out = subprocess.check_output(
            ["systemctl", "show", "-p", "MemoryMax", "-p", "MemoryHigh", f"openclaw-{agent}.service"],
            text=True, timeout=5,
        )
        memmax = None
        memhi = None
        for line in out.splitlines():
            if line.startswith("MemoryMax="):
                v = line.split("=", 1)[1].strip()
                if v and v != "infinity":
                    memmax = v
            elif line.startswith("MemoryHigh="):
                v = line.split("=", 1)[1].strip()
                if v and v != "infinity":
                    memhi = v
        return memmax, memhi
    except Exception:
        return None, None


_ENVVAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

def systemctl_environment(agent):
    """Return parsed Environment= dict for openclaw-<agent>.service.

    Returns {} on failure. Used to resolve ${VAR} references in openclaw.json
    so we don't hash template strings (which falsely collide across agents)."""
    try:
        out = subprocess.check_output(
            ["systemctl", "show", "-p", "Environment", f"openclaw-{agent}.service"],
            text=True, timeout=5,
        )
        env = {}
        for line in out.splitlines():
            if not line.startswith("Environment="):
                continue
            raw = line.split("=", 1)[1]
            # Format: "KEY1=val1 KEY2=val2" — split on whitespace, then on first '='
            for tok in raw.split():
                if "=" in tok:
                    k, v = tok.split("=", 1)
                    env[k] = v
        return env
    except Exception:
        return {}


def resolve_token(raw, env):
    """If raw is a single ${VAR} template, return env[VAR]; else return raw.

    Bare literals pass through unchanged. Unresolved templates return ""
    (so they hash to None via the empty-check upstream)."""
    if not raw:
        return raw
    m = _ENVVAR_RE.fullmatch(raw)
    if not m:
        return raw
    return env.get(m.group(1), "")


def aggregate():
    agents = sorted([
        p.split("openclaw-")[-1]
        for p in glob.glob("/opt/gcg/openclaw-*")
        if os.path.isdir(p) and not p.endswith(".bak")
    ])
    records = {}
    gw_hashes = {}
    hk_hashes = {}

    for a in agents:
        rec = {"agent": a, "issues": []}
        ocj = f"/opt/gcg/openclaw-{a}/openclaw.json"
        mfp = f"/run/openclaw-{a}/manifest.json"
        am_paths = [
            f"/opt/gcg/openclaw-{a}/workspace/AGENTS.md",
            f"/opt/gcg/openclaw-{a}/AGENTS.md",
        ]

        if os.path.exists(ocj):
            try:
                d = json.load(open(ocj))
                defaults = d.get("agents", {}).get("defaults", {})
                model = defaults.get("model", {})
                rec["model_primary"]   = model.get("primary")
                rec["model_fallbacks"] = model.get("fallbacks", [])
                rec["heartbeat"]       = defaults.get("heartbeat", {})
                rec["workspace"]       = defaults.get("workspace")
                rec["timezone"]        = defaults.get("userTimezone")
                rec["gateway_port"]    = d.get("gateway", {}).get("port")

                gw_token_raw   = d.get("gateway", {}).get("auth", {}).get("token") or ""
                hooks_tok_raw  = d.get("hooks", {}).get("token") or ""
                tg         = d.get("channels", {}).get("telegram", {})
                tg_token   = tg.get("botToken") or ""

                # Resolve ${VAR} from systemd Environment= so we hash actual tokens, not template strings
                # (template strings falsely collide across agents — caused 2026-05-25 incident)
                svc_env = systemctl_environment(a)
                gw_token  = resolve_token(gw_token_raw, svc_env)
                hooks_tok = resolve_token(hooks_tok_raw, svc_env)

                # Only hash if token actually has content (avoids sha12("")==e3b0c44298fc false-positives)
                rec["gateway_token_sha"] = hashlib.sha256(gw_token.encode()).hexdigest()[:12] if gw_token else None
                rec["hooks_token_sha"]   = hashlib.sha256(hooks_tok.encode()).hexdigest()[:12] if hooks_tok else None
                rec["gateway_token_source"] = "env" if gw_token_raw.startswith("${") else "literal"
                rec["hooks_token_source"]   = "env" if hooks_tok_raw.startswith("${") else "literal"
                rec["gateway_token_empty"] = (gw_token == "")
                rec["hooks_token_empty"]   = (hooks_tok == "")
                rec["telegram_bot_id"]   = tg_token.split(":")[0] if ":" in tg_token else None
                rec["telegram_bot_token_plaintext"] = bool(tg_token) and not tg_token.startswith("${")
                rec["telegram_enabled"]  = tg.get("enabled", False)
                rec["telegram_groups"]   = list(tg.get("groups", {}).keys()) if tg.get("groups") else []
                rec["auth_profiles"]     = list(d.get("auth", {}).get("profiles", {}).keys())
                rec["agents_list"]       = [
                    item.get("name") if isinstance(item, dict) else None
                    for item in d.get("agents", {}).get("list", [])
                ]
                rec["mcp_servers"]       = list((d.get("plugins") or {}).get("entries", {}).keys()) + list((d.get("mcpServers") or {}).keys())

                # Crash constraint: tokens equal AND both non-empty
                if gw_token and hooks_tok and gw_token == hooks_tok:
                    rec["issues"].append("FATAL: hooks.token == gateway.auth.token (crash loop)")
                if rec["gateway_token_sha"]:
                    gw_hashes.setdefault(rec["gateway_token_sha"], []).append(a)
                if rec["hooks_token_sha"]:
                    hk_hashes.setdefault(rec["hooks_token_sha"], []).append(a)
            except Exception as e:
                rec["issues"].append(f"openclaw.json parse: {e}")
        else:
            rec["issues"].append("openclaw.json missing")

        if os.path.exists(mfp):
            try:
                m = json.load(open(mfp))
                rec["db_role"]    = m.get("db_role")
                rec["env_keys"]   = m.get("env_keys", [])
                rec["google_sa"]  = m.get("cred_files", {}).get("google_sa", False)
                rec["anthropic_profile"] = m.get("cred_files", {}).get("anthropic_auth_profile", False)
            except Exception as e:
                rec["issues"].append(f"manifest parse: {e}")
        else:
            rec["issues"].append("manifest.json missing")

        # systemd MemoryMax (from systemctl, not drop-in glob)
        memmax, memhi = systemctl_memorymax(a)
        rec["memory_max"]  = memmax
        rec["memory_high"] = memhi

        # validator drop-in presence
        di_files = glob.glob(f"/etc/systemd/system/openclaw-{a}.service.d/*.conf")
        has_validate = False
        for f in di_files:
            try:
                if "validate-agent-config" in open(f).read():
                    has_validate = True
                    break
            except Exception:
                pass
        rec["has_validator"] = has_validate

        # AGENTS.md
        rec["agents_md"] = next((p for p in am_paths if os.path.exists(p)), None)

        # tier classification
        if a in TIER_A: rec["tier"] = "A"
        elif a in TIER_C: rec["tier"] = "C"
        elif a in TIER_D: rec["tier"] = "D"
        else: rec["tier"] = "B"
        rec["critical"] = a in CRITICAL

        records[a] = rec

    # token-group resolution
    shared_gw = {h: members for h, members in gw_hashes.items() if len(members) > 1}
    shared_hk = {h: members for h, members in hk_hashes.items() if len(members) > 1}

    for rec in records.values():
        h = rec.get("gateway_token_sha")
        if h and h in shared_gw:
            rec["channel_token_policy"] = f"shared:group-{h}"
            rec["channel_token_shared_with"] = [a for a in shared_gw[h] if a != rec["agent"]]
        elif h:
            rec["channel_token_policy"] = "per_agent"
            rec["channel_token_shared_with"] = []
        else:
            rec["channel_token_policy"] = "missing"
            rec["channel_token_shared_with"] = []

        h = rec.get("hooks_token_sha")
        if h and h in shared_hk:
            rec["hooks_token_policy"] = f"shared:group-{h}"
            rec["hooks_token_shared_with"] = [a for a in shared_hk[h] if a != rec["agent"]]
        elif h:
            rec["hooks_token_policy"] = "per_agent"
            rec["hooks_token_shared_with"] = []
        else:
            rec["hooks_token_policy"] = "missing"
            rec["hooks_token_shared_with"] = []

    return records, shared_gw, shared_hk


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    records, shared_gw, shared_hk = aggregate()
    # atomic write
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        json.dump(records, f, indent=2)
    os.replace(tmp, OUT)
    os.chmod(OUT, 0o600)

    print(f"wrote {OUT}")
    print(f"agents: {len(records)}")
    print(f"shared gateway token groups: {len(shared_gw)}")
    for h, members in shared_gw.items():
        print(f"  {h}: {sorted(members)}")
    print(f"shared hooks token groups: {len(shared_hk)}")
    for h, members in shared_hk.items():
        print(f"  {h}: {len(members)} agents (showing first 5): {sorted(members)[:5]}{'...' if len(members) > 5 else ''}")
    issues = {a: r["issues"] for a, r in records.items() if r["issues"]}
    if issues:
        print(f"issues: {issues}")
    else:
        print("issues: none")


if __name__ == "__main__":
    main()
