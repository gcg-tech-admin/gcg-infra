#!/usr/bin/env python3
# claude-jsonl-extract.py — v31 bridge helper (Atlas, 2026-06-11)
# Extract the latest assistant turn text from Claude Code JSONL session files.
# Reads $HOME/.claude/projects/<slug>/*.jsonl and returns assistant text blocks
# whose record timestamp is >= --since (UTC epoch). Preserves newlines exactly.
#
# Modes:
#   check   -> exit 0 if >=1 assistant text record since `since`, else exit 1
#   extract -> print concatenated assistant text since `since`
import sys, os, json, glob, argparse
from datetime import datetime


def parse_ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def collect(project_dir, since):
    texts = []  # (ts, text)
    if not os.path.isdir(project_dir):
        return texts
    for fp in glob.glob(os.path.join(project_dir, "*.jsonl")):
        try:
            with open(fp, "r", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line or '"assistant"' not in line:
                        continue
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    if o.get("type") != "assistant":
                        continue
                    ts = parse_ts(o.get("timestamp", ""))
                    if ts < since:
                        continue
                    m = o.get("message", {})
                    c = m.get("content")
                    buf = ""
                    if isinstance(c, list):
                        for b in c:
                            if isinstance(b, dict) and b.get("type") == "text":
                                buf += b.get("text", "")
                    elif isinstance(c, str):
                        buf += c
                    if buf.strip():
                        texts.append((ts, buf))
        except Exception:
            continue
    texts.sort(key=lambda x: x[0])
    return texts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-dir", required=True)
    ap.add_argument("--since", type=float, required=True)
    ap.add_argument("--mode", choices=["check", "extract"], default="extract")
    a = ap.parse_args()
    texts = collect(a.project_dir, a.since)
    if a.mode == "check":
        sys.exit(0 if texts else 1)
    out = "\n".join(t for _, t in texts).strip()
    sys.stdout.write(out)


if __name__ == "__main__":
    main()
