#!/usr/bin/env python3
"""
Per-Operation Activation Router (R2) — Shape C Gate Logic.

Determines whether an operation targets the shared DB and thus should
trigger Shape C (live schema introspection) loading.

Usage:
  # Check if an operation needs Shape C:
  python scripts/activation-router.py --operation "read zones"
  python scripts/activation-router.py --operation "write fees"
  python scripts/activation-router.py --operation "merge contact"
  python scripts/activation-router.py --operation "deploy nginx"

  # Show current activation state for all known packs:
  python scripts/activation-router.py --list-packs

  # Show why a decision was made:
  python scripts/activation-router.py --operation "write deal_key_facts" --verbose

Exit codes:
  0 — Shape NOT needed (no DB-scoped operation detected)
  42 — Shape C needed (DB-scoped operation detected)
  1 — Error

Embed in agent as:
  ```python
  import subprocess
  result = subprocess.run(
      ['python', 'scripts/activation-router.py', '--operation', op_desc, '--json'],
      capture_output=True, text=True
  )
  router_output = json.loads(result.stdout)
  if router_output['shape'] == 'C':
      # Load live introspection tool
  ```
"""
import argparse
import json
import os
import re
import sys

# ── Known DB tables and their operation patterns ───────────────────
# Each entry: (pattern, weight, pack)
DB_TARGET_PATTERNS = [
    # Shared DB - public schema (table names)
    (r'\b(freezones?|free_?zone)\b', 10, 'shared-db-schema'),
    (r'\b(zones?|zone_details|designated_zones?)\b', 10, 'shared-db-schema'),
    (r'\b(packages|package_fees|freezone_packages)\b', 10, 'shared-db-schema'),
    (r'\b(fees?|fee_schedules?|freezone_fee_schedule)\b', 10, 'shared-db-schema'),
    (r'\b(entities|entity_relationships)\b', 10, 'shared-db-schema'),
    (r'\b(users|roles|permissions)\b', 10, 'shared-db-schema'),
    (r'\b(individuals|companies)\b', 10, 'shared-db-schema'),
    (r'\b(legislation|tax_rules|dta_agreements?)\b', 10, 'shared-db-schema'),
    (r'\b(memories|audit_log)\b', 8, 'shared-db-schema'),

    # Shared DB - meridian schema
    (r'\b(deals?)\b', 10, 'shared-db-schema'),
    (r'\b(crm_contacts|contact_context|contact_patterns|contact_state)\b', 10, 'shared-db-schema'),
    (r'\b(deal_key_facts|owner_targets|ownership_transfers)\b', 10, 'shared-db-schema'),
    (r'\b(crm_deals?|crm_deal_assignment|clients)\b', 8, 'shared-db-schema'),

    # General DB keywords (lower confidence)
    (r'\b(select|insert|update|delete|from|join|where)\b', 5, 'shared-db-schema'),
    (r'\b(database|db|schema|table|column|query)\b', 3, 'shared-db-schema'),
    (r'\b(sql|psql|postgres|pg_)\b', 3, 'shared-db-schema'),

    # Superapp operations
    (r'\b(superapp|super-app|deal_facts)\b', 10, 'superapp-data-model'),
    (r'\b(consolidat(e|ion))\b', 5, 'superapp-data-model'),
    (r'\b(staging.*deal|prod.*deal|id.*conflat)\b', 5, 'superapp-data-model'),
]

# Operations that explicitly DO NOT target the shared DB
EXCLUDED_OPERATIONS = [
    r'\b(deploy|build|docker|container|image)\b',
    r'\b(nginx|proxy|reverse.?proxy)\b',
    r'\b(git|commit|push|merge|branch)\b',
    r'\b(cron|schedule|heartbeat)\b',
    r'\b(config|restart|start|stop|status)\b',
]


def matches_pattern(text, pattern_list):
    """Check text against a list of (pattern, weight, pack) tuples.
    Returns list of (pack, weight, matched_text) for all matches.
    """
    results = []
    for pattern, weight, pack in pattern_list:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            results.append((pack, weight, m.group(0)))
    return results


def check_excluded(text):
    """Check if operation is explicitly excluded from DB targeting."""
    for pattern in EXCLUDED_OPERATIONS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def classify_operation(operation_desc, verbose=False):
    """Classify an operation description into Shape A/B/C.

    Returns dict with:
      - shape: 'A' | 'B' | 'C' (C = needs live introspection)
      - reasoning: list of reasons for the decision
      - matches: list of (pack, weight, matched_text)
      - score: total confidence score
    """
    op_lower = operation_desc.lower()
    reasoning = []
    matches = []

    # Step 1: Check exclusion
    if check_excluded(op_lower):
        reasoning.append(f'Operation excluded: matches infra/deploy pattern')
        return {'shape': 'A', 'reasoning': reasoning, 'matches': [], 'score': 0}

    # Step 2: Check for DB target patterns
    matches = matches_pattern(op_lower, DB_TARGET_PATTERNS)
    if not matches:
        reasoning.append('No DB target patterns detected in operation description')
        return {'shape': 'A', 'reasoning': reasoning, 'matches': [], 'score': 0}

    # Step 3: Score the matches
    score = sum(w for _, w, _ in matches)
    unique_packs = set(p for p, _, _ in matches)

    # Step 4: Determine shape
    # Shape C = confident DB operation (score >= 8 = hit at least one specific table)
    # Shape B = moderate (score 3-7, mostly DB-adjacent keywords)
    # Shape A = non-DB

    if score >= 8:
        shape = 'C'
        packs_str = ', '.join(unique_packs)
        table_hits = [f'{m[2]} (weight={m[1]})' for m in matches if m[1] >= 8]
        reasoning.append(f'Score {score} >= 8: confirmed DB operation targeting {packs_str}')
        if table_hits:
            reasoning.append(f'  Table-level hits: {", ".join(table_hits)}')
    elif score >= 3:
        shape = 'B'
        packs_str = ', '.join(unique_packs)
        reasoning.append(f'Score {score} >= 3: DB-adjacent operation targeting {packs_str}')
        reasoning.append('  Not confident enough for Shape C (live introspect)')
    else:
        shape = 'A'
        reasoning.append(f'Score {score} < 3: too vague for DB targeting')

    if verbose:
        reasoning.append(f'  Matches: {matches}')

    return {
        'shape': shape,
        'reasoning': reasoning,
        'matches': [{'pack': p, 'weight': w, 'matched': m} for p, w, m in matches],
        'score': score,
    }


def main():
    parser = argparse.ArgumentParser(
        description='Per-Operation Activation Router (R2) — Shape C Gate Logic',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Examples:\n'
            '  python scripts/activation-router.py --operation "read zones"\n'
            '  python scripts/activation-router.py --operation "write deal_key_facts" --verbose\n'
            '  python scripts/activation-router.py --operation "deploy nginx"\n'
            '  python scripts/activation-router.py --list-packs\n'
            '\n'
            'Exit: 0 = Shape A/B (no live introspect needed), 42 = Shape C (needs live introspect)\n'
        )
    )
    parser.add_argument('--operation', '-o', help='Operation description to classify')
    parser.add_argument('--verbose', '-v', action='store_true', help='Detailed reasoning')
    parser.add_argument('--json', action='store_true', help='JSON output')
    parser.add_argument('--list-packs', action='store_true', help='List known packs and their patterns')
    args = parser.parse_args()

    if args.list_packs:
        # Show known packs
        packs = {}
        for pattern, weight, pack in DB_TARGET_PATTERNS:
            packs.setdefault(pack, []).append((pattern, weight))

        output = {'known_packs': {}}
        for pack_name, patterns in sorted(packs.items()):
            output['known_packs'][pack_name] = [
                {'pattern': p, 'weight': w} for p, w in sorted(patterns, key=lambda x: -x[1])
            ]

        if args.json:
            print(json.dumps(output, indent=2))
        else:
            print('# Known Packs & Activation Patterns')
            print()
            for pack_name, patterns in sorted(output['known_packs'].items()):
                print(f'## {pack_name}')
                print(f'| Pattern | Weight |')
                print(f'|---------|--------|')
                for p in patterns:
                    print(f'| `{p["pattern"]}` | {p["weight"]} |')
                print()
        return

    if not args.operation:
        parser.print_help()
        sys.exit(1)

    result = classify_operation(args.operation, verbose=args.verbose)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        shape_emoji = {'C': '🔴', 'B': '🟡', 'A': '🟢'}
        print(f'{shape_emoji[result["shape"]]} Shape {result["shape"]}  (score={result["score"]})')
        print()
        for r in result['reasoning']:
            print(f'  {r}')
        print()
        print(f'  {"→ Load live introspection tool" if result["shape"] == "C" else "→ No live introspection needed"}')

    # Exit code: 42 = Shape C
    sys.exit(42 if result['shape'] == 'C' else 0)


if __name__ == '__main__':
    main()
