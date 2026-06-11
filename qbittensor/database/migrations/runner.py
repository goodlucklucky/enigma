# The MIT License (MIT)
# Copyright © 2026 qBitTensor Labs
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the “Software”), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.
#
# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

from __future__ import annotations

"""
Generic, versioned database migration runner for Enigma.

Design goals:
- Simple and self-contained (no external dependencies like Alembic).
- Safe to run on every startup.
- Idempotent.
- Supports separate migration sets for "validator" and "miner" scopes.
- Easy to add new migrations as Python modules.
- Clear logging of what was applied.

This system is intended to be the standard way all future schema changes are delivered.
"""

import importlib
import logging
import pkgutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


from sqlalchemy import Column, Integer, String, DateTime, text, func
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker
from qbittensor.utils.services.telemetry import TelemetryService

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Migration metadata table (created automatically if missing)
# --------------------------------------------------------------------------- #

MigrationBase = declarative_base()


class SchemaMigration(MigrationBase):
    """Tracks which migrations have been applied."""

    __tablename__ = "schema_migrations"

    version = Column(Integer, primary_key=True)
    description = Column(String(255), nullable=False)
    applied_at = Column(DateTime, nullable=False, server_default=func.now())


# --------------------------------------------------------------------------- #
# Data structures
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Migration:
    version: int
    description: str
    upgrade: Callable[[Engine], None]
    downgrade: Callable[[Engine], None] | None = None


# --------------------------------------------------------------------------- #
# Core runner
# --------------------------------------------------------------------------- #


class MigrationRunner:
    """
    Generic migration runner.

    Usage:
        runner = MigrationRunner(scope="validator")
        runner.run(engine)
    """

    def __init__(self, scope: str = "validator", telemetry_service: "TelemetryService | None" = None):
        """
        Args:
            scope: Logical scope of migrations ("validator" or "miner").
                   Used to select which package of migrations to load.
            telemetry_service: Optional telemetry service for dumping data during
                               special migrations (e.g. pre-update snapshots of verified tx cache).
        """
        self.scope = scope.lower()
        self.telemetry_service: "TelemetryService | None" = telemetry_service
        self._migrations: list[Migration] | None = None

    def _get_migrations_package(self) -> str:
        if self.scope == "validator":
            return "qbittensor.database.migrations.versions.validator"
        elif self.scope == "miner":
            return "qbittensor.database.migrations.versions.miner"
        else:
            raise ValueError(f"Unknown migration scope: {self.scope}")

    def _discover_migrations(self) -> list[Migration]:
        """Dynamically import all migration modules for this scope and collect them."""
        package_name = self._get_migrations_package()
        migrations: list[Migration] = []

        try:
            package = importlib.import_module(package_name)
        except ModuleNotFoundError:
            logger.debug(f"No migration package found for scope '{self.scope}' at {package_name}")
            return []

        for module_info in pkgutil.iter_modules(package.__path__):
            if not module_info.name.startswith(("0", "1", "2", "3", "4", "5", "6", "7", "8", "9")):
                continue  # Only numbered migration files

            full_name = f"{package_name}.{module_info.name}"
            mod = importlib.import_module(full_name)

            version = getattr(mod, "VERSION", None)
            description = getattr(mod, "DESCRIPTION", module_info.name)
            upgrade = getattr(mod, "upgrade", None)
            downgrade = getattr(mod, "downgrade", None)

            if version is None or upgrade is None:
                logger.warning(f"Skipping invalid migration module: {full_name}")
                continue

            migrations.append(
                Migration(
                    version=int(version),
                    description=str(description),
                    upgrade=upgrade,
                    downgrade=downgrade,
                )
            )

        migrations.sort(key=lambda m: m.version)
        return migrations

    def _ensure_migrations_table(self, engine: Engine) -> None:
        """Create the schema_migrations table if it does not exist."""
        MigrationBase.metadata.create_all(bind=engine, tables=[SchemaMigration.__table__])

    def _get_applied_versions(self, engine: Engine) -> set[int]:
        """Return the set of already-applied migration versions."""
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT version FROM schema_migrations ORDER BY version")
            )
            return {row[0] for row in result}

    def run(self, engine: Engine) -> None:
        """
        Run all pending migrations for this scope in order.
        Safe to call multiple times.
        """
        self._ensure_migrations_table(engine)
        applied = self._get_applied_versions(engine)

        if self._migrations is None:
            self._migrations = self._discover_migrations()

        pending = [m for m in self._migrations if m.version not in applied]

        if not pending:
            logger.debug(f"[{self.scope}] Database is up to date (no pending migrations).")
            return

        logger.info(f"[{self.scope}] Running {len(pending)} pending migration(s)...")

        Session = sessionmaker(bind=engine)

        for migration in pending:
            logger.info(f"[{self.scope}] Applying migration {migration.version}: {migration.description}")

            try:
                migration.upgrade(engine, telemetry_service=self.telemetry_service)

                with Session() as session:
                    session.add(
                        SchemaMigration(
                            version=migration.version,
                            description=migration.description,
                        )
                    )
                    session.commit()

                logger.info(f"[{self.scope}] Successfully applied migration {migration.version}")

            except Exception as e:
                logger.exception(f"[{self.scope}] FAILED to apply migration {migration.version}: {e}")
                raise

        logger.info(f"[{self.scope}] All migrations completed successfully.")


# --------------------------------------------------------------------------- #
# Convenience entry point used by DBConnection
# --------------------------------------------------------------------------- #


def run_migrations_for_db(engine: Engine, database_name_prefix: str, telemetry_service: "TelemetryService | None" = None) -> None:
    """
    Decide which migration scope to run based on the database being opened.

    This is the function called from DBConnection during startup.
    """
    if database_name_prefix == "challenge_solutions":
        runner = MigrationRunner(scope="validator", telemetry_service=telemetry_service)
        runner.run(engine)
    elif database_name_prefix == "miner_submissions":
        runner = MigrationRunner(scope="miner", telemetry_service=telemetry_service)
        runner.run(engine)
    else:
        # Unknown / future DB types – run nothing but don't crash
        logger.debug(f"No migration scope defined for database prefix '{database_name_prefix}'")
