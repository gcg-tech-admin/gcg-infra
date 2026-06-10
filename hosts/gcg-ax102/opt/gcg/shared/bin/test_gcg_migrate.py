#!/usr/bin/env python3
"""
Unit tests for gcg-migrate.
Connects to staging DB as gcg_migration role.

Tests:
  (a) collision on existing object → exit 1
  (b) CREATE without ADR entry → exit 1
  (c) drift (simulated by munging schema-of-record.json) → exit 1
  (d) clean, registered migration → exit 0 (dry-run)
  (e) bypass without env var → exit 1
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TEST_DIR = os.path.dirname(os.path.abspath(__file__))
GCG_MIGRATE = os.path.join(TEST_DIR, 'gcg-migrate')
SCHEMA_OF_RECORD = '/opt/gcg/shared/docs/schema/schema-of-record.json'
ADR_REGISTRY = '/opt/gcg/shared/docs/schema/adr-registry.json'

TEST_MIGRATION_CONTENT = 'CREATE TABLE gcg_migrate_test_dummy (id int);'
VALID_MIGRATION_ID = '006_tool_call_log'


def run_migrate(args, input_text=None):
    """Run gcg-migrate with args. Returns (returncode, stdout+stderr)."""
    cmd = [sys.executable, GCG_MIGRATE] + args
    result = subprocess.run(cmd, capture_output=True, text=True, input=input_text,
                            timeout=60)
    return result.returncode, result.stdout + result.stderr


class TestGcgMigrateCollision(unittest.TestCase):
    """Test (a): collision on existing object → exit 1."""

    def test_collision_on_existing_table(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sql', delete=False) as f:
            f.write('CREATE TABLE memories (id int);')
            f.flush()
            rc, output = run_migrate([f.name, '--db', 'staging', '--dry-run'])
            os.unlink(f.name)
        self.assertNotEqual(rc, 0, f'Expected non-zero exit, got {rc}')
        self.assertIn('no ADR entry', output, f'Expected ADR rejection in: {output[:200]}')


class TestGcgMigrateMissingADR(unittest.TestCase):
    """Test (b): CREATE without ADR entry → exit 1."""

    def test_missing_adr_rejected(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sql', delete=False) as f:
            f.write('CREATE TABLE z_test_no_adr_xyz (id int);')
            f.flush()
            rc, output = run_migrate([f.name, '--db', 'staging', '--dry-run'])
            os.unlink(f.name)
        self.assertNotEqual(rc, 0, f'Expected non-zero exit, got {rc}')
        self.assertIn('no ADR entry', output)


class TestGcgMigrateDrift(unittest.TestCase):
    """Test (c): drift → exit 1."""

    def test_drift_detected(self):
        # Backup original schema-of-record
        bak = SCHEMA_OF_RECORD + '.bak'
        shutil.copy2(SCHEMA_OF_RECORD, bak)

        try:
            # Mung the file by removing a table
            with open(SCHEMA_OF_RECORD) as f:
                data = json.load(f)
            if data['tables']:
                removed = data['tables'].pop(0)
            with open(SCHEMA_OF_RECORD, 'w') as f:
                json.dump(data, f, indent=2)

            with tempfile.NamedTemporaryFile(mode='w', suffix='.sql', delete=False) as f:
                f.write(TEST_MIGRATION_CONTENT)
                f.flush()
                rc, output = run_migrate([f.name, '--db', 'staging', '--dry-run'])
                os.unlink(f.name)
            self.assertNotEqual(rc, 0, f'Expected non-zero exit, got {rc}')
            self.assertIn('drift', output.lower(), f'Expected drift message in: {output[:200]}')
        finally:
            shutil.move(bak, SCHEMA_OF_RECORD)


class TestGcgMigrateClean(unittest.TestCase):
    """Test (d): clean, registered, replay-detectable migration → exit 0."""

    def test_clean_migration(self):
        rc, output = run_migrate([
            '006_tool_call_log.sql', '--db', 'staging', '--dry-run',
        ])
        self.assertEqual(rc, 0, f'Expected exit 0, got {rc}: {output[:500]}')
        self.assertIn('Replay detected', output)


class TestGcgMigrateBypass(unittest.TestCase):
    """Test (e): bypass without env var → exit 1."""

    def test_bypass_no_secret(self):
        # Unset GCG_DDL_BYPASS if set
        env = os.environ.copy()
        env.pop('GCG_DDL_BYPASS', None)

        with tempfile.NamedTemporaryFile(mode='w', suffix='.sql', delete=False) as f:
            f.write(TEST_MIGRATION_CONTENT)
            f.flush()
            cmd = [sys.executable, GCG_MIGRATE, f.name, '--db', 'staging', '--dry-run', '--bypass']
            result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=30)
            os.unlink(f.name)
        self.assertNotEqual(result.returncode, 0, f'Expected non-zero exit')
        self.assertIn('GCG_DDL_BYPASS', result.stdout + result.stderr)


class TestGcgMigrateAnnotate(unittest.TestCase):
    """Test (f): gcg-migrate-annotate CLI."""

    def test_annotate_cli_works(self):
        cmd = ['gcg-migrate-annotate', '006_tool_call_log', '--db', 'staging']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        self.assertEqual(
            result.returncode, 0,
            f'Expected exit 0 from annotate: {result.stderr[:500]}'
        )

    def test_annotate_bad_id_fails(self):
        cmd = ['gcg-migrate-annotate', 'nonexistent_migration_xyz', '--db', 'staging']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('not found', result.stderr)


class TestGcgMigrateAnnotateNoADR(unittest.TestCase):
    """Test (g): annotate --help works."""

    def test_annotate_help(self):
        result = subprocess.run(
            ['gcg-migrate-annotate', '--help'],
            capture_output=True, text=True, timeout=10
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn('usage:', result.stdout)


if __name__ == '__main__':
    unittest.main()
