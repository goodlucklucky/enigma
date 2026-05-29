"""
Generic database migration framework for Enigma.

This package provides a simple, versioned, safe-to-run-on-startup migration system.
It is designed to be the standard mechanism for all future schema changes.
"""

from .runner import MigrationRunner, run_migrations_for_db

__all__ = ["MigrationRunner", "run_migrations_for_db"]
