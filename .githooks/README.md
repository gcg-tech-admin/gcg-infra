# .githooks — Infra Fix Registry Hooks

Tracked git hooks for the fix registry. These live in `.githooks/` (version-controlled) rather than `.git/hooks/` so every collaborator gets them.

## Activation (one-time per clone)

```bash
cd /opt/gcg/infra-repo
git config core.hooksPath .githooks
```

Verify:

```bash
git config core.hooksPath
# → .githooks
```

## Hooks

### `post-commit`
Triggers when any `fixes/fix-*.md` file is part of the last commit.

- Reads all `fixes/fix-*.md` files (skips `TEMPLATE.md`, `CHANGELOG.md`, `EVENT-VERIFIED.md`, `ROLLBACK.md`, `fix-schema.yaml`).
- Extracts YAML frontmatter fields: `id`, `date`, `agent`, `severity`, `systems`, `symptom`.
- Rebuilds `fixes/INDEX.md` as a date-descending markdown table.
- Amends the last commit with the updated `INDEX.md` so the index stays in sync without a separate commit.
- Retries the amend once after 1 s if a lock collision occurs.

**Infinite recursion guard:** the hook sets `GIT_POST_COMMIT_HOOK_RUNNING=1` before the amend call and exits early if already set.

### `pre-push`
Triggers before any push. Inspects all `fixes/fix-*.md` files in the push range.

Validates each file:
- YAML frontmatter is present and parses correctly.
- Required fields present: `id`, `date`, `agent`, `severity`, `status`, `systems`, `symptom`, `root_cause`, `solution`.
- `id` matches `^fix-\d{4}-\d{2}-\d{2}-[a-z0-9-]+$`.
- `agent` in `[daen, vulcan, argus, talos, mnemosyne]`.
- `severity` in `[crit, warn, info]`.
- `status` in `[resolved, partial, reverted]`.

Exits `1` (blocks push) if any file fails. Error output goes to stderr.

## Dependencies

Both hooks require **Python 3** on `PATH`. No third-party libraries needed.

## Bypass (emergency only)

```bash
# Skip pre-push validation — use only when deliberately pushing a draft entry
git push --no-verify

# Prevent post-commit amend (e.g. during rebase scripting)
GIT_POST_COMMIT_HOOK_RUNNING=1 git commit ...
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `INDEX.md` not updating | `core.hooksPath` not set | `git config core.hooksPath .githooks` |
| Push blocked on valid file | Frontmatter uses tabs, not spaces | Convert to spaces; YAML is whitespace-sensitive |
| `python3: command not found` | Python not on PATH | Install `python3` or symlink |
| Amend fails with lock error | Concurrent git process | Wait and retry; hook retries once automatically |
