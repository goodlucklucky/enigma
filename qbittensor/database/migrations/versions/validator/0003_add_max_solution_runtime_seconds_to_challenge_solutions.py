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
Migration 0003: Add 'max_solution_runtime_seconds' column to challenge_solutions table.

This stores the milestone's configured max_solution_runtime (in seconds) at the
time a solution execution is accepted. It is fetched from the platform's
milestone configuration early in the verified execution path.

The container manager uses the stored value (looked up via solution_id label
on containers) to enforce timeouts, avoiding repeated API calls per check.
Legacy rows (created before this column) will have NULL and fall back to
per-milestone API lookup for timeout during their remaining lifetime.
"""

from sqlalchemy import text

VERSION = 3
DESCRIPTION = "Add 'max_solution_runtime_seconds' integer column to challenge_solutions"


def upgrade(engine, telemetry_service=None):
    """Add the max_solution_runtime_seconds column (nullable, no backfill)."""
    with engine.connect() as conn:
        # Check if the table even exists yet (for fresh DBs, migrations run before create_all)
        table_check = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='challenge_solutions'")
        )
        if not table_check.fetchone():
            # Table doesn't exist yet → create_all() will create it with the column from the model.
            # New rows will get NULL (or explicit value at insert time).
            return

        # Check if column already exists (idempotent / re-runs)
        result = conn.execute(text("PRAGMA table_info(challenge_solutions)"))
        columns = [row[1] for row in result]
        if "max_solution_runtime_seconds" not in columns:
            conn.execute(text("""
                ALTER TABLE challenge_solutions
                ADD COLUMN max_solution_runtime_seconds INTEGER
            """))
            # No UPDATE/backfill: existing in-flight rows keep NULL so the runtime
            # enforcement path can fall back to API lookup for them.
            conn.commit()


def downgrade(engine):
    """Downgrade is not fully supported (SQLite DROP COLUMN limitations).
    In a real deployment you would recreate the table without the column.
    """
    # Intentionally a no-op / not implemented for simplicity, matching prior migrations.
    pass
