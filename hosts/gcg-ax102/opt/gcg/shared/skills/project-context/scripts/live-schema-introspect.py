#!/usr/bin/env python3
"""
Live Schema Introspection Tool — READ-ONLY, on-demand.

Agent-callable CLI for live DB schema introspection.
Deliberately lightweight — introspects only what's asked, not the full DB.
Returns markdown (default) or JSON.

Usage (agent):
  python scripts/live-schema-introspect.py --table zones          # single table detail
  python scripts/live-schema-introspect.py --schema public       # all public tables
  python scripts/live-schema-introspect.py --tables zones,packages,fees  # specific tables
  python scripts/live-schema-introspect.py --search client       # full-text search
  python scripts/live-schema-introspect.py --summary             # compact overview
  python scripts/live-schema-introspect.py --all                 # full schema dump
  python scripts/live-schema-introspect.py --json                # JSON output (any flag)

Contract (SKILL.md ref): agent calls this ON DEMAND for live schema context.
Does NOT write files — stdout only. Read-only credentials always.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

EXCLUDED_SCHEMAS = ('pg_catalog', 'information_schema', 'pg_toast')


def get_connection(host=None):
    """Read-only DB connection using per-agent creds.

    Args:
        host: Override host. If None, tries localhost then 10.0.0.2 (vSwitch to AX42).
    """
    import psycopg2

    agent = os.environ.get('GCG_DB_AGENT', 'talos')
    pwd_file = f'/opt/gcg/shared/credentials/db/gcg_{agent}.pwd'
    if not os.path.exists(pwd_file):
        pwd_file = '/opt/gcg/shared/credentials/db/gcg_talos.pwd'
    with open(pwd_file) as f:
        password = f.read().strip()

    if host is None:
        host = os.environ.get('GCG_DB_HOST', 'localhost')

    cfg = dict(
        dbname='gcg_intelligence',
        user=f'gcg_{agent}',
        password=password,
        host=host,
        port=int(os.environ.get('GCG_DB_PORT', '5432')),
    )

    return psycopg2.connect(**cfg)


def qry(cur, q, params=None):
    cur.execute(q, params)
    return cur.fetchall()


def qryone(cur, q, params=None):
    cur.execute(q, params)
    r = cur.fetchone()
    return r[0] if r else None


# ── Introspection helpers ──────────────────────────────────────────

def list_schemas(conn):
    cur = conn.cursor()
    rows = qry(cur, """
        SELECT nspname FROM pg_catalog.pg_namespace
        WHERE nspname NOT IN %s AND nspname NOT LIKE 'pg_%'
        ORDER BY nspname
    """, (EXCLUDED_SCHEMAS,))
    cur.close()
    return [r[0] for r in rows]


def list_tables(conn, schema=None, search=None, full=False):
    """List tables with basic metadata. Returns list of dicts."""
    cur = conn.cursor()
    if search:
        rows = qry(cur, """
            SELECT t.table_schema, t.table_name,
                   pg_catalog.obj_description(c.oid, 'pg_class') AS table_comment,
                   pg_size_pretty(pg_total_relation_size(c.oid)) AS table_size,
                   (SELECT count(*) FROM information_schema.columns
                    WHERE table_schema=t.table_schema AND table_name=t.table_name) AS col_count
            FROM information_schema.tables t
            JOIN pg_catalog.pg_class c ON c.relname = t.table_name
                AND c.relnamespace = quote_ident(t.table_schema)::regnamespace
            WHERE t.table_type = 'BASE TABLE'
              AND t.table_schema NOT IN %s
              AND (t.table_name ILIKE '%%' || %s || '%%'
                   OR t.table_schema ILIKE '%%' || %s || '%%'
                   OR pg_catalog.obj_description(c.oid, 'pg_class') ILIKE '%%' || %s || '%%')
            ORDER BY t.table_schema, t.table_name
        """, (EXCLUDED_SCHEMAS, search, search, search))
    elif schema:
        rows = qry(cur, """
            SELECT t.table_schema, t.table_name,
                   pg_catalog.obj_description(c.oid, 'pg_class') AS table_comment,
                   pg_size_pretty(pg_total_relation_size(c.oid)) AS table_size,
                   (SELECT count(*) FROM information_schema.columns
                    WHERE table_schema=t.table_schema AND table_name=t.table_name) AS col_count
            FROM information_schema.tables t
            JOIN pg_catalog.pg_class c ON c.relname = t.table_name
                AND c.relnamespace = quote_ident(t.table_schema)::regnamespace
            WHERE t.table_type = 'BASE TABLE'
              AND t.table_schema NOT IN %s
              AND t.table_schema = %s
            ORDER BY t.table_name
        """, (EXCLUDED_SCHEMAS, schema))
    else:
        rows = qry(cur, """
            SELECT t.table_schema, t.table_name,
                   pg_catalog.obj_description(c.oid, 'pg_class') AS table_comment,
                   pg_size_pretty(pg_total_relation_size(c.oid)) AS table_size,
                   (SELECT count(*) FROM information_schema.columns
                    WHERE table_schema=t.table_schema AND table_name=t.table_name) AS col_count
            FROM information_schema.tables t
            JOIN pg_catalog.pg_class c ON c.relname = t.table_name
                AND c.relnamespace = quote_ident(t.table_schema)::regnamespace
            WHERE t.table_type = 'BASE TABLE'
              AND t.table_schema NOT IN %s
            ORDER BY t.table_schema, t.table_name
        """, (EXCLUDED_SCHEMAS,))

    cur.close()
    result = []
    for schema, table, comment, size, col_count in rows:
        result.append({
            'schema': schema,
            'table': table,
            'comment': comment or '',
            'size': size,
            'column_count': col_count,
        })
    return result


def introspect_table_detail(conn, schema, table_name):
    """Full detail for one table: columns, indexes, FKs."""
    cur = conn.cursor()
    result = {'schema': schema, 'table': table_name}

    # Comment + size
    result['comment'] = qryone(cur, """
        SELECT pg_catalog.obj_description(c.oid, 'pg_class')
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relname = %s
    """, (schema, table_name)) or ''

    result['size'] = qryone(cur, """
        SELECT pg_size_pretty(pg_total_relation_size(quote_ident(%s) || '.' || quote_ident(%s)::regclass))
    """, (schema, table_name)) or '?'

    result['row_estimate'] = qryone(cur, """
        SELECT n_live_tup FROM pg_stat_user_tables
        WHERE schemaname = %s AND relname = %s
    """, (schema, table_name)) or 0

    # Columns
    cols = qry(cur, """
        SELECT column_name, data_type, is_nullable, column_default,
               pgd.description AS column_comment
        FROM information_schema.columns c
        LEFT JOIN pg_catalog.pg_description pgd
            ON pgd.objsubid = c.ordinal_position
            AND pgd.objoid = (quote_ident(c.table_schema) || '.' || quote_ident(c.table_name))::regclass::oid
        WHERE c.table_schema = %s AND c.table_name = %s
        ORDER BY c.ordinal_position
    """, (schema, table_name))
    result['columns'] = [
        {'name': r[0], 'type': r[1], 'nullable': r[2], 'default': r[3], 'comment': r[4] or ''}
        for r in cols
    ]

    # Indexes
    idxs = qry(cur, """
        SELECT indexname, indexdef FROM pg_indexes
        WHERE schemaname = %s AND tablename = %s
        ORDER BY indexname
    """, (schema, table_name))
    result['indexes'] = [{'name': r[0], 'definition': r[1]} for r in idxs]

    # Foreign keys
    fks = qry(cur, """
        SELECT tc.constraint_name, kcu.column_name,
               ccu.table_schema AS foreign_schema,
               ccu.table_name AS foreign_table,
               ccu.column_name AS foreign_column
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
            ON tc.constraint_name = kcu.constraint_name
            AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage ccu
            ON tc.constraint_name = ccu.constraint_name
        WHERE tc.constraint_type = 'FOREIGN KEY'
            AND tc.table_schema = %s
            AND tc.table_name = %s
    """, (schema, table_name))
    result['foreign_keys'] = [
        {'name': r[0], 'column': r[1], 'ref_schema': r[2], 'ref_table': r[3], 'ref_column': r[4]}
        for r in fks
    ]

    cur.close()
    return result


def introspect_all_detail(conn):
    """Full introspect of all non-system tables."""
    tables = list_tables(conn)
    result = []
    for t in tables:
        detail = introspect_table_detail(conn, t['schema'], t['table'])
        result.append(detail)
    return result


# ── Search helpers ─────────────────────────────────────────────────

def search_columns(conn, term):
    """Search for columns matching a term across all tables."""
    cur = conn.cursor()
    rows = qry(cur, """
        SELECT table_schema, table_name, column_name, data_type,
               pgd.description AS column_comment
        FROM information_schema.columns c
        LEFT JOIN pg_catalog.pg_description pgd
            ON pgd.objsubid = c.ordinal_position
            AND pgd.objoid = (quote_ident(c.table_schema) || '.' || quote_ident(c.table_name))::regclass::oid
        WHERE c.table_schema NOT IN %s
          AND (c.column_name ILIKE '%%' || %s || '%%'
               OR c.data_type ILIKE '%%' || %s || '%%')
        ORDER BY table_schema, table_name, ordinal_position
    """, (EXCLUDED_SCHEMAS, term, term))
    cur.close()
    return [
        {'schema': r[0], 'table': r[1], 'column': r[2], 'type': r[3], 'comment': r[4] or ''}
        for r in rows
    ]


# ── Format output ──────────────────────────────────────────────────

def format_table_list_md(tables):
    lines = [f'# Tables ({len(tables)})', '']
    lines.append('| Schema | Table | Comment | Size | Columns |')
    lines.append('|--------|-------|---------|------|---------|')
    for t in tables:
        c = t['comment'][:60].replace('\n', ' ').replace('|', '\\|') if t['comment'] else '—'
        lines.append(f'| {t["schema"]} | `{t["table"]}` | {c} | {t["size"]} | {t["column_count"]} |')
    return '\n'.join(lines)


def format_summary_md(tables):
    """Compact one-line-per-schema overview."""
    from collections import Counter
    schema_counts = Counter(t['schema'] for t in tables)
    total_cols = sum(t['column_count'] for t in tables)

    lines = [
        '# Live Schema Summary',
        '',
        f'{len(tables)} tables, {total_cols} columns across {len(schema_counts)} schemas.',
        '',
        '| Schema | Tables |',
        '|--------|-------|',
    ]
    for s in sorted(schema_counts):
        lines.append(f'| `{s}` | {schema_counts[s]} |')
    return '\n'.join(lines)


def format_table_detail_md(detail):
    lines = [
        f'# {detail["schema"]}.{detail["table"]}',
        '',
    ]
    if detail['comment']:
        lines.append(f'_Comment: {detail["comment"]}_')
    lines.append(f'_Size: {detail["size"]}  |  Rows (est): {detail["row_estimate"]}_')
    lines.append('')

    # Columns
    cols = detail['columns']
    lines.append(f'**Columns ({len(cols)})**')
    lines.append('')
    lines.append('| # | Column | Type | Nullable | Default | Comment |')
    lines.append('|---|--------|------|----------|---------|---------|')
    for i, c in enumerate(cols, 1):
        d = str(c['default'] or '')[:40].replace('\n', ' ').replace('|', '\\|')
        cm = str(c['comment'] or '')[:60].replace('\n', ' ').replace('|', '\\|')
        lines.append(f'| {i} | `{c["name"]}` | `{c["type"]}` | {c["nullable"]} | {d} | {cm} |')
    lines.append('')

    # Indexes
    idxs = detail.get('indexes', [])
    if idxs:
        lines.append(f'**Indexes ({len(idxs)})**')
        lines.append('')
        for ix in idxs:
            lines.append(f'- `{ix["name"]}`: `{ix["definition"]}`')
        lines.append('')

    # Foreign keys
    fks = detail.get('foreign_keys', [])
    if fks:
        lines.append(f'**Foreign Keys ({len(fks)})**')
        lines.append('')
        for fk in fks:
            lines.append(f'- `{fk["name"]}`: `{fk["column"]}` → `{fk["ref_schema"]}`.`{fk["ref_table"]}`.`{fk["ref_column"]}`')
        lines.append('')

    return '\n'.join(lines)


def format_search_results_md(tables_found, cols_found):
    lines = []
    if tables_found:
        lines.append(f'## Matching Tables ({len(tables_found)})')
        lines.append('')
        lines.append('| Schema | Table | Comment | Size |')
        lines.append('|--------|-------|---------|------|')
        for t in tables_found:
            c = t['comment'][:60].replace('|', '\\|') if t['comment'] else '—'
            lines.append(f'| {t["schema"]} | `{t["table"]}` | {c} | {t["size"]} |')
        lines.append('')

    if cols_found:
        lines.append(f'## Matching Columns ({len(cols_found)})')
        lines.append('')
        lines.append('| Schema | Table | Column | Type | Comment |')
        lines.append('|--------|-------|--------|------|---------|')
        for c in cols_found:
            cm = c['comment'][:60].replace('|', '\\|') if c['comment'] else '—'
            lines.append(f'| {c["schema"]} | `{c["table"]}` | `{c["column"]}` | `{c["type"]}` | {cm} |')
        lines.append('')

    if not tables_found and not cols_found:
        lines.append('_No matches found._')

    return '\n'.join(lines)


# ── Main ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Live DB Schema Introspection Tool (read-only, on-demand)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Examples:\n'
            '  python scripts/live-schema-introspect.py --table zones\n'
            '  python scripts/live-schema-introspect.py --schema public\n'
            '  python scripts/live-schema-introspect.py --tables zones,packages\n'
            '  python scripts/live-schema-introspect.py --search client\n'
            '  python scripts/live-schema-introspect.py --summary\n'
            '  python scripts/live-schema-introspect.py --all --json\n'
        )
    )

    # One of these mutually-exclusive-ish modes:
    parser.add_argument('--table', '-t', help='Single table detail (schema.table or just table)')
    parser.add_argument('--tables', '-T', help='Comma-separated table names (scoped to --schema or public)')
    parser.add_argument('--schema', '-s', help='Schema name to list tables')
    parser.add_argument('--search', help='Search tables and columns by keyword')
    parser.add_argument('--summary', action='store_true', help='Compact schema overview')
    parser.add_argument('--all', action='store_true', help='Full dump of all tables')

    # Modifiers
    parser.add_argument('--json', action='store_true', help='JSON output (machine-readable)')
    parser.add_argument('--host', default=None, help='DB host override')

    args = parser.parse_args()

    # Connect
    try:
        conn = get_connection(host=args.host)
        conn.set_session(readonly=True, autocommit=True)
    except Exception as e:
        print(f'ERROR: Cannot connect to DB: {e}', file=sys.stderr)
        sys.exit(1)

    try:
        # ── Route by mode ──────────────────────────────────────

        if args.table:
            # Parse schema.table or just table
            if '.' in args.table:
                schema, table_name = args.table.split('.', 1)
            else:
                schema = 'public'
                table_name = args.table
            detail = introspect_table_detail(conn, schema, table_name)
            if args.json:
                print(json.dumps(detail, indent=2))
            else:
                print(format_table_detail_md(detail))

        elif args.tables:
            table_names = [t.strip() for t in args.tables.split(',') if t.strip()]
            schema = args.schema or 'public'
            details = []
            for tn in table_names:
                details.append(introspect_table_detail(conn, schema, tn))
            if args.json:
                print(json.dumps(details, indent=2))
            else:
                for d in details:
                    print(format_table_detail_md(d))
                    print('---')
                    print()

        elif args.search:
            tables_found = list_tables(conn, search=args.search)
            cols_found = search_columns(conn, args.search)
            if args.json:
                print(json.dumps({'tables': tables_found, 'columns': cols_found}, indent=2))
            else:
                print(format_search_results_md(tables_found, cols_found))

        elif args.summary:
            tables = list_tables(conn)
            if args.json:
                print(json.dumps(tables, indent=2))
            else:
                print(format_summary_md(tables))

        elif args.schema:
            tables = list_tables(conn, schema=args.schema)
            if args.json:
                print(json.dumps(tables, indent=2))
            else:
                print(format_table_list_md(tables))

        elif args.all:
            details = introspect_all_detail(conn)
            if args.json:
                print(json.dumps(details, indent=2))
            else:
                for d in details:
                    print(format_table_detail_md(d))
                    print('---')
                    print()

        else:
            # Default: summary
            tables = list_tables(conn)
            if args.json:
                print(json.dumps(tables, indent=2))
            else:
                print(format_summary_md(tables))

    finally:
        conn.close()


if __name__ == '__main__':
    main()
