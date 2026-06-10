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


def upgrade(engine, telemetry_service=None):
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
