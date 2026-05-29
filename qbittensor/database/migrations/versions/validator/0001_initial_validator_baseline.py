"""
Initial baseline migration for the validator (challenge_solutions) database.

This migration is intentionally minimal. It exists to establish the migration
system and the schema_migrations table itself.

Future migrations will be added as 0002_*, 0003_*, etc.

Because no real production validator submissions exist yet at the time of this
change, we have a clean window to evolve the schema without legacy data concerns.
"""

VERSION = 1
DESCRIPTION = "Establish migration system and baseline for validator DB"


def upgrade(engine):
    """
    This migration is effectively a no-op for table structure.

    The schema_migrations table is created automatically by the MigrationRunner
    before any upgrade functions are called. All actual table creation for the
    initial models still happens via Base.metadata.create_all() in DBConnection
    (this is safe and idempotent).
    """
    # Intentionally left empty.
    # Future migrations will contain real ALTER TABLE / CREATE INDEX statements
    # or more complex data migrations using raw SQL or SQLAlchemy Core.
    pass


def downgrade(engine):
    """Downgrade is not supported for this baseline migration."""
    pass
