#!/usr/bin/env python3
"""fleet-yaml-gen — generate FLEET.yaml v2.1 from live state + roster.

Reads /opt/gcg/shared/state/fleet-records.json (from fleet-aggregate.py)
+ /opt/gcg/shared/config/fleet-roster.yaml (Peter-curated metadata).
Writes /opt/gcg/shared/FLEET.yaml.v2_1.draft (or path from --out).

Usage:
  fleet-yaml-gen                        # write draft
  fleet-yaml-gen --dry-run              # print to stdout, no write
  fleet-yaml-gen --out PATH             # alt output path
  fleet-yaml-gen --force                # bypass staleness check
"""
import argparse, glob, json, os, shutil, sys, time, datetime, re
from pathlib import Path
from ruamel.yaml import YAML

ROSTER  = "/opt/gcg/shared/config/fleet-roster.yaml"
RECORDS = "/opt/gcg/shared/state/fleet-records.json"
OUT_DEFAULT = "/opt/gcg/shared/FLEET.yaml.v2_1.draft"

REFRESH_KEYS_ORDER = [
    "title", "group", "tier", "role", "critical",
    "host", "port", "workspace", "agents_md",
    "model_primary", "model_fallbacks", "expected_model_primary", "heartbeat",
    "db_role", "keys", "google_sa", "anthropic_profile",
    "channel_token_policy", "channel_token_shared_with",
    "gateway_token_sha", "hooks_token_sha",
    "hooks_token_policy", "hooks_token_shared_with",
    "telegram_bot", "telegram_bot_id", "telegram_enabled", "telegram_groups",
    "telegram_bot_token_plaintext",
    "subagents", "mcp_servers", "memory_max", "has_validator",
    "op_sa_persisted",
    "serves", "human_email", "human_telegram", "mission",
]

DEPRECATED = ["flow", "raj", "farzan"]


def check_staleness(records_path, force=False):
    """Abort if any openclaw.json is newer than records (records are stale)."""
    if force: return
    if not os.path.exists(records_path):
        sys.exit(f"ERROR: {records_path} missing — run /opt/gcg/shared/bin/fleet-aggregate.py first")
    rec_mtime = os.path.getmtime(records_path)
    stale = []
    for p in glob.glob("/opt/gcg/openclaw-*/openclaw.json"):
        if os.path.getmtime(p) > rec_mtime:
            stale.append(p)
    if stale:
        sys.exit(f"ERROR: records stale; {len(stale)} openclaw.json files newer:\n  " +
                 "\n  ".join(stale[:5]) +
                 ("\n  ..." if len(stale) > 5 else "") +
                 "\nrerun /opt/gcg/shared/bin/fleet-aggregate.py first (or pass --force)")


def derive_canonical_keys(env_keys):
    out = set()
    if "ANTHROPIC_API_KEY" in env_keys:    out.add("anthropic")
    if "DEEPSEEK_API_KEY" in env_keys:     out.add("deepseek")
    if "MOONSHOT_API_KEY" in env_keys:     out.add("moonshot")
    if "GEMINI_API_KEY" in env_keys:       out.add("gemini")
    if "OPENAI_OAUTH_TOKEN" in env_keys:   out.add("openai_oauth")
    return out


def build_agents(recs, ex_agents):
    """Build per-agent dict for v2.1 output."""
    out = {}
    for name in sorted(recs.keys()):
        r = recs[name]
        ex = ex_agents.get(name, {})

        canonical_keys = derive_canonical_keys(set(r.get("env_keys", [])))

        # op_sa_persisted reflects live state but CLAUDE.md says daen+talos should be true
        # We record live truth; validator flags the drift.
        op_sa = "OP_SERVICE_ACCOUNT_TOKEN" in r.get("env_keys", [])

        out[name] = {
            "title": ex.get("title"),
            "group": ex.get("group"),
            "tier": r["tier"],
            "role": ex.get("role"),
            "critical": r["critical"],
            "host": "ax102",
            "port": r.get("gateway_port"),
            "workspace": r.get("workspace") or f"/opt/gcg/openclaw-{name}/workspace",
            "agents_md": r.get("agents_md"),
            "model_primary": r.get("model_primary"),
            "model_fallbacks": r.get("model_fallbacks", []),
            "expected_model_primary": ex.get("model_primary"),  # from roster — used for drift
            "heartbeat": r.get("heartbeat", {}),
            "db_role": r.get("db_role"),
            "keys": sorted(canonical_keys),
            "google_sa": r.get("google_sa", False),
            "anthropic_profile": r.get("anthropic_profile", False),
            "channel_token_policy": r.get("channel_token_policy"),
            "channel_token_shared_with": r.get("channel_token_shared_with", []),
            "gateway_token_sha": r.get("gateway_token_sha"),
            "hooks_token_sha": r.get("hooks_token_sha"),
            "hooks_token_policy": r.get("hooks_token_policy"),
            "hooks_token_shared_with": r.get("hooks_token_shared_with", []),
            "telegram_bot": ex.get("telegram_bot"),
            "telegram_bot_id": r.get("telegram_bot_id"),
            "telegram_enabled": r.get("telegram_enabled", False),
            "telegram_groups": r.get("telegram_groups", []),
            "telegram_bot_token_plaintext": r.get("telegram_bot_token_plaintext", False),
            "subagents": r.get("agents_list", []),
            "mcp_servers": r.get("mcp_servers", []),
            "memory_max": r.get("memory_max"),
            "has_validator": r.get("has_validator", False),
            "op_sa_persisted": op_sa,
            "serves": ex.get("serves"),
            "human_email": ex.get("human_email"),
            "human_telegram": ex.get("human_telegram"),
            "mission": ex.get("mission"),
        }
    return out


def yaml_format_value(k, v):
    """Format one key:value pair as YAML, aggressively quoting strings."""
    if isinstance(v, bool):
        return f"    {k}: {str(v).lower()}\n"
    if isinstance(v, list):
        return f"    {k}: []\n" if not v else f"    {k}: {json.dumps(v)}\n"
    if isinstance(v, dict):
        return f"    {k}: {{}}\n" if not v else f"    {k}: {json.dumps(v)}\n"
    if isinstance(v, int):
        return f"    {k}: {v}\n"
    sv = str(v)
    needs_quote = (
        sv == "" or
        sv[0] in "?-*&!|>%@`,[]{}#" or
        any(c in sv for c in [":", "#", "'", '"']) or
        sv.lower() in ("yes", "no", "true", "false", "null", "~", "on", "off")
    )
    if needs_quote:
        sv = json.dumps(sv)
    return f"    {k}: {sv}\n"


def build_yaml(out_agents, drift, recs, ex_top, version_meta):
    now = version_meta["now"]
    critical_list = sorted([n for n, r in out_agents.items() if r["critical"]])

    # banned models from roster's model_policy (strip inline comments)
    banned_models = []
    if "model_policy" in ex_top:
        mp = ex_top["model_policy"]
        if isinstance(mp, dict):
            raw = mp.get("banned", []) or mp.get("blocked", []) or []
            for item in raw:
                # roster has "deepseek/deepseek-v4-pro  # 94% hallucination..." — strip comment
                clean = re.split(r"\s*#", str(item), 1)[0].strip()
                if clean:
                    banned_models.append(clean)

    hdr = f"""# ════════════════════════════════════════════════════════════════════════════════
# GCG FLEET v2.1 — CANONICAL CONFIGURATION (LIVE-STATE REFRESH)
# ════════════════════════════════════════════════════════════════════════════════
# Refreshed: {now}
# Generator: /opt/gcg/shared/bin/fleet-yaml-gen.py
# Aggregator: /opt/gcg/shared/bin/fleet-aggregate.py
# Records:    /opt/gcg/shared/state/fleet-records.json
# Source of truth: live state (openclaw.json + manifest.json + systemctl)
# Previous: FLEET.yaml v2.0 (2026-05-04, pre-incident, STALE)
#
# To regenerate after fleet changes:
#   ssh root@77.42.7.80 'fleet-aggregate.py && fleet-yaml-gen --out FLEET.yaml.v2_1.draft'
#
# Promotion (after fleet-validate green):
#   fleet-promote /opt/gcg/shared/FLEET.yaml.v2_1.draft
# ════════════════════════════════════════════════════════════════════════════════

version: "2.1"
schema_version: "canonical-fleet-2026-05-20"
last_updated: "{now}"
generated_by: "fleet-yaml-gen.py (live-state refresh)"
total_agents: {len(out_agents)}
critical_agents: {json.dumps(critical_list)}
deprecated_agents: {json.dumps(DEPRECATED)}

restart_policy:
  critical_last: true
  sequential_critical: true
  gap_seconds: 10
  max_parallel: 5
  critical_agents: {json.dumps(critical_list)}

model_policy:
  banned: {json.dumps(banned_models) if banned_models else "[]"}
  banned_note: |
    deepseek/deepseek-v4-pro MUST be called with thinking=high (FLEET-WIDE BINDING RULE 2026-05-10).
    Used bare it is on banned list. openrouter/* banned fleet-wide.

shared_token_groups:
  gateway:
"""
    shared_gw = {}
    for name, r in out_agents.items():
        pol = r.get("channel_token_policy") or ""
        if pol.startswith("shared:"):
            shared_gw.setdefault(pol, []).append(name)
    for grp, members in sorted(shared_gw.items()):
        hdr += f"    {grp}: {json.dumps(sorted(members))}\n"

    hdr += "  hooks:\n"
    shared_hk = {}
    for name, r in out_agents.items():
        pol = r.get("hooks_token_policy") or ""
        if pol.startswith("shared:"):
            shared_hk.setdefault(pol, []).append(name)
    for grp, members in sorted(shared_hk.items()):
        hdr += f"    {grp}: {json.dumps(sorted(members))}\n"

    # openclaw_json_schema — consumed by /opt/gcg/shared/scripts/validate-agent-config.py
    # If FLEET.yaml is unreadable, validator falls back to identical hardcoded defaults (safe).
    hdr += '\nopenclaw_json_schema:\n'
    hdr += '  allowed_root_keys: ["meta","env","wizard","browser","auth","agents","tools","commands","hooks","channels","gateway","skills","plugins","messages","diagnostics","logging","audio","media","session","cron","web","discovery","memory","mcp","approvals","broadcast","bindings","models","secrets","acp","ui","update"]\n'
    hdr += '  valid_embedding_providers: ["openai","gemini","voyage","mistral","bedrock","lmstudio","ollama","local"]\n'

    # Carry-through top-level sections from roster that we don't synthesize ourselves.
    # Dropping these was a v2.0 → v2.1 regression (red-team finding).
    for section in ("humans", "infrastructure", "network", "dispatch", "shared_inboxes", "agent_emails"):
        if section in ex_top and ex_top[section]:
            hdr += f"\n{section}:\n"
            # Use ruamel to round-trip structured content; fall back to json indented.
            try:
                from ruamel.yaml import YAML
                from io import StringIO
                y2 = YAML(typ="safe", pure=True)
                y2.default_flow_style = False
                y2.indent(mapping=2, sequence=4, offset=2)
                buf = StringIO()
                y2.dump({section: ex_top[section]}, buf)
                # strip the section header line we just added; ruamel re-emits it
                lines = buf.getvalue().splitlines()
                if lines and lines[0].startswith(f"{section}:"):
                    lines = lines[1:]
                hdr += "\n".join(lines) + "\n"
            except Exception:
                # Fallback: json-ish dump indented two spaces
                import json as _json
                jv = _json.dumps(ex_top[section], indent=2)
                # indent every line by 2 spaces
                hdr += "\n".join("  " + ln for ln in jv.splitlines()) + "\n"

    hdr += "\nknown_drift:\n"
    hdr += "  # documented gaps between live state and the operating mandate.\n"
    hdr += "  - op_sa_persisted_should_be_true: [daen, talos]  # CLAUDE.md says yes; live false on all.\n"
    if banned_models:
        hdr += "  - banned_models_in_fallbacks: validator emits WARN until openclaw.json scrubbed\n"
    hdr += "  - plaintext_telegram_bot_tokens: openclaw.json channels.telegram.botToken on 28/29 agents (zero-plaintext violation, task #87)\n"
    hdr += "  - plaintext_db_pwd_files: /opt/gcg/shared/credentials/db/gcg_*.pwd on persistent disk (task #87)\n"

    hdr += "\ndrift_from_previous_version:\n"
    for d in drift[:60]:
        hdr += f"  - {json.dumps(d)}\n"

    out_yaml = hdr + "\nagents:\n"
    for name in sorted(out_agents.keys()):
        r = out_agents[name]
        out_yaml += f"\n  {name}:\n"
        for k in REFRESH_KEYS_ORDER:
            if k not in r:
                continue
            v = r[k]
            if v is None:
                continue
            out_yaml += yaml_format_value(k, v)
    return out_yaml


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=OUT_DEFAULT, help=f"output path (default: {OUT_DEFAULT})")
    ap.add_argument("--dry-run", action="store_true", help="print to stdout, no write")
    ap.add_argument("--force", action="store_true", help="skip staleness check")
    args = ap.parse_args()

    check_staleness(RECORDS, args.force)

    recs = json.load(open(RECORDS))

    yaml_lib = YAML(typ="safe", pure=True)
    ex_top = yaml_lib.load(open(ROSTER)) or {}
    ex_agents = ex_top.get("agents", {}) or {}

    out_agents = build_agents(recs, ex_agents)

    drift = []
    for name, rec in recs.items():
        ex = ex_agents.get(name, {})
        if ex:
            if ex.get("model_primary") and rec.get("model_primary") and ex["model_primary"] != rec.get("model_primary"):
                drift.append(f"{name}: model_primary roster={ex['model_primary']} live={rec['model_primary']}")
            if ex.get("port") and rec.get("gateway_port") and ex["port"] != rec["gateway_port"]:
                drift.append(f"{name}: port roster={ex['port']} live={rec['gateway_port']}")
        else:
            drift.append(f"{name}: NEW agent (not in roster)")
    for name in ex_agents:
        if name not in recs and name not in DEPRECATED:
            drift.append(f"{name}: REMOVED (in roster but not live)")

    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out_yaml = build_yaml(out_agents, drift, recs, ex_top, {"now": now})

    if args.dry_run:
        sys.stdout.write(out_yaml)
        return

    # bak snapshot if OUT exists
    if os.path.exists(args.out):
        bak = f"{args.out}.bak.{int(time.time())}"
        shutil.copy2(args.out, bak)
        print(f"snapshot: {bak}")

    # atomic write
    tmp = args.out + ".tmp"
    with open(tmp, "w") as f:
        f.write(out_yaml)
    os.replace(tmp, args.out)
    print(f"wrote {args.out}")
    print(f"agents: {len(out_agents)}")
    print(f"drift items: {len(drift)}")
    for d in drift[:30]:
        print(f"  - {d}")


if __name__ == "__main__":
    main()
