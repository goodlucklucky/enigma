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
Migration 0002: Add 'cleaned' column to challenge_solutions table.

This column is used to track whether a solution's containers and filesystem
artifacts have been cleaned up (on normal completion, startup recovery,
or when paths are found to be missing).

This allows the solution management logic to be driven primarily by the
solution record's primary key (id) rather than reverse lookups by container_name,
container_id, or paths.
"""

from sqlalchemy import text

VERSION = 2
DESCRIPTION = "Add 'cleaned' boolean column to challenge_solutions"


def upgrade(engine):
    """Add the cleaned column.

    - For brand new DBs: the table will be created later by create_all() with the column (default false for new rows).
    - For existing DBs that have the table but lack the column: add it (backfills with DEFAULT 0), then set all *existing* rows to cleaned=1 (true). This treats pre-migration records as "already cleaned/legacy" so the new startup recovery logic doesn't try to clean old artifacts.
    """
    with engine.connect() as conn:
        # Check if the table even exists yet (for fresh DBs, migrations run before create_all)
        table_check = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='challenge_solutions'")
        )
        if not table_check.fetchone():
            # Table doesn't exist yet → create_all() will create it with the column from the model.
            # New rows will get default=False.
            return

        # Check if column already exists (idempotent / re-runs)
        result = conn.execute(text("PRAGMA table_info(challenge_solutions)"))
        columns = [row[1] for row in result]
        if "cleaned" not in columns:
            conn.execute(text("""
                ALTER TABLE challenge_solutions
                ADD COLUMN cleaned BOOLEAN NOT NULL DEFAULT 0
            """))
            # Now set all rows that existed before this column (i.e. all current rows)
            # to cleaned = true. This marks legacy data as "already handled".
            conn.execute(text("UPDATE challenge_solutions SET cleaned = 1"))
            conn.commit()


def downgrade(engine):
    """Downgrade is not fully supported (SQLite DROP COLUMN limitations).
    In a real deployment you would recreate the table without the column.
    """
    # Intentionally a no-op / not implemented for simplicity, matching baseline style.
    # If needed in future, implement table recreate + data copy.
    pass
