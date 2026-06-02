# Fix Registry — Rollback Protocol

Procedures for undoing bad fix entries, recovering a corrupt INDEX.md, or reverting the entire registry state.

---

## 1. Revert a specific fix entry

Use `git revert` — never force-push or `reset --hard` on a shared branch.

```bash
# Find the commit that added the bad entry
git log --oneline fixes/fix-YYYY-MM-DD-slug.md

# Revert it (creates a new commit)
git revert <commit-sha>

# The post-commit hook will fire and regenerate INDEX.md automatically.
# Verify:
cat fixes/INDEX.md | grep fix-YYYY-MM-DD-slug  # should be gone
```

If the entry spans multiple commits:

```bash
git revert <sha1> <sha2> ...
```

---

## 2. Regenerate INDEX.md manually

If INDEX.md is corrupt, missing, or out of sync (e.g. after a rebase or manual edit):

```bash
cd /opt/gcg/infra-repo

# Create an empty commit to fire post-commit hook, or run the script directly:
bash .githooks/post-commit

# Alternatively, touch any fix file and amend:
git commit --allow-empty -m "chore: force INDEX.md regen"
```

Or run the regeneration inline without any commit:

```bash
cd /opt/gcg/infra-repo/fixes
python3 - <<'EOF'
import os, re
from datetime import datetime, timezone

fixes_dir = "."
rows = []

for fname in sorted(os.listdir(fixes_dir)):
    if not (fname.startswith("fix-") and fname.endswith(".md")):
        continue
    with open(os.path.join(fixes_dir, fname)) as f:
        content = f.read()
    m = re.match(r'^---\s*\n(.*?\n)---', content, re.DOTALL)
    if not m:
        continue
    block = m.group(1)
    def get(key):
        mo = re.search(rf'^{key}:\s*"?([^"\n]+)"?\s*$', block, re.MULTILINE)
        return mo.group(1).strip() if mo else ''
    def get_list(key):
        mo = re.search(rf'^{key}:\s*\n((?:\s+-\s+.+\n)*)', block, re.MULTILINE)
        if not mo: return get(key)
        return ', '.join(i.strip().strip('"') for i in re.findall(r'-\s+"?([^"\n]+)"?', mo.group(1)))
    rows.append((get('date')[:10], get('id'), get('agent'), get('severity'), get_list('systems'), get('symptom')))

rows.sort(key=lambda r: r[0], reverse=True)
now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')
lines = [
    "# Fix Registry Index", "",
    f"Last updated: {now} UTC", "",
    "| ID | Date | Agent | Severity | System | Symptom |",
    "|----|------|-------|----------|--------|---------|",
]
for fid, *_ in [('id','date','agent','sev','sys','sym')]:
    pass  # header already written
for date, fid, agent, severity, systems, symptom in rows:
    lines.append(f"| {fid} | {date} | {agent} | {severity} | {systems} | {symptom} |")
lines += ["", "*Sorted by date descending.*", "*Schema: fix-schema.yaml | Template: TEMPLATE.md | Rollback: ROLLBACK.md*"]
with open("INDEX.md", "w") as f:
    f.write('\n'.join(lines) + '\n')
print("INDEX.md regenerated.")
EOF
```

---

## 3. Detect bad entries (confidence audit)

Run this to find any fix file that fails schema validation without doing a push:

```bash
cd /opt/gcg/infra-repo

for f in fixes/fix-*.md; do
  result=$(python3 - "$f" <<'PYEOF'
import sys, re
path = sys.argv[1]
fname = path.split('/')[-1]
with open(path) as f:
    content = f.read()
m = re.match(r'^---\s*\n(.*?\n)---', content, re.DOTALL)
if not m:
    print(f"FAIL {fname}: no frontmatter"); sys.exit(0)
block = m.group(1)
def get(k):
    mo = re.search(rf'^{k}:\s*"?([^"\n]+)"?\s*$', block, re.MULTILINE)
    return mo.group(1).strip() if mo else None
required = ['id','date','agent','severity','status','systems','symptom','root_cause','solution']
missing = [k for k in required if get(k) is None and not re.search(rf'^{k}:', block, re.MULTILINE)]
if missing:
    print(f"FAIL {fname}: missing {missing}"); sys.exit(0)
fid = get('id') or ''
agent = get('agent') or ''
severity = get('severity') or ''
status = get('status') or ''
errs = []
if not re.match(r'^fix-\d{4}-\d{2}-\d{2}-[a-z0-9-]+$', fid): errs.append(f"bad id: {fid}")
if agent not in ('daen','vulcan','argus','talos','mnemosyne'): errs.append(f"bad agent: {agent}")
if severity not in ('crit','warn','info'): errs.append(f"bad severity: {severity}")
if status not in ('resolved','partial','reverted'): errs.append(f"bad status: {status}")
if errs: print(f"FAIL {fname}: {'; '.join(errs)}")
else: print(f"OK   {fname}")
PYEOF
  )
  echo "$result"
done
```

---

## 4. Manual recovery — worst case

If the git history itself is corrupt or the registry is in an unrecoverable state:

```bash
# 1. Find the last known-good tag or commit
git log --oneline --decorate fixes/

# 2. Check out the registry at that point (read-only inspection)
git show <good-sha>:fixes/INDEX.md

# 3. Restore only the fixes directory from a known-good commit
git checkout <good-sha> -- fixes/

# 4. Stage and commit the restoration
git add fixes/
git commit -m "fix: restore fixes/ from known-good state at <good-sha>"
```

---

## 5. Rollback checklist

- [ ] Identify the bad entry (file name + commit SHA)
- [ ] `git revert <sha>` on a feature branch
- [ ] Confirm INDEX.md regenerated correctly after revert commit
- [ ] Run confidence audit (section 3) — zero FAIL lines
- [ ] PR to main; tag rollback commit: `git tag rollback-<date>-<slug>`
- [ ] Notify the agent who filed the bad entry via fleet inbox
