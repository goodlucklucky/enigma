"""
Tests for the generic DB migration runner.

These tests verify:
- The runner creates the schema_migrations table.
- It correctly discovers and applies numbered migrations.
- It is idempotent (safe to run multiple times).
- Validator vs miner scope behavior.
"""

import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from qbittensor.database.migrations.runner import MigrationRunner, run_migrations_for_db


class TestMigrationRunner:
    def test_validator_scope_creates_schema_migrations_table_and_applies_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test_validator.db"
            engine = create_engine(f"sqlite:///{db_path}")

            runner = MigrationRunner(scope="validator")
            runner.run(engine)

            with engine.connect() as conn:
                # Table should exist
                tables = [r[0] for r in conn.execute(
                    text('SELECT name FROM sqlite_master WHERE type="table"')
                )]
                assert "schema_migrations" in tables

                # Baseline migration v1 should be recorded
                rows = list(conn.execute(
                    text("SELECT version, description FROM schema_migrations ORDER BY version")
                ))
                assert len(rows) >= 1
                assert rows[0][0] == 1

    def test_runner_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test_idempotent.db"
            engine = create_engine(f"sqlite:///{db_path}")

            runner = MigrationRunner(scope="validator")
            runner.run(engine)
            runner.run(engine)  # second run must not blow up

            with engine.connect() as conn:
                rows = list(conn.execute(text("SELECT COUNT(*) FROM schema_migrations")))
                # Should still only have the migrations that exist, no duplicates
                assert rows[0][0] >= 1

    def test_miner_scope_does_not_create_migration_table(self):
        """Per approved plan, we do not require miner migrations at this time."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test_miner.db"
            engine = create_engine(f"sqlite:///{db_path}")

            run_migrations_for_db(engine, "miner_submissions")

            with engine.connect() as conn:
                tables = [r[0] for r in conn.execute(
                    text('SELECT name FROM sqlite_master WHERE type="table"')
                )]
                # We expect the framework to have done nothing for miner scope
                assert "schema_migrations" not in tables

    def test_unknown_scope_is_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test_unknown.db"
            engine = create_engine(f"sqlite:///{db_path}")

            # Should not raise
            run_migrations_for_db(engine, "some_future_db_type")

            # No migration table should have been created
            with engine.connect() as conn:
                tables = [r[0] for r in conn.execute(
                    text('SELECT name FROM sqlite_master WHERE type="table"')
                )]
                assert "schema_migrations" not in tables
