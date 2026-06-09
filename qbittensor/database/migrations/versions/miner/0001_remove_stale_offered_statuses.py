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
Migration 0001 (miner scope): Remove stale OFFERED statuses recorded before archive lookup.

This is a one-time data cleanup migration (no schema change). It is safe to re-run.
The cutoff is intentionally fixed to the approximate time of the behavior change.
"""

from sqlalchemy import text

VERSION = 1
DESCRIPTION = "Remove pre-archive OFFERED statuses"

CUTOFF_ISO = "2026-06-09 20:00:00"


def upgrade(engine):
    """Delete OFFERED miner_submission_statuses rows older than the cutoff."""
    with engine.connect() as conn:
        # Only act if the table exists (fresh miner DBs have no data to clean).
        table_check = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='miner_submission_statuses'")
        )
        if not table_check.fetchone():
            return

        # Delete only the OFFERED statuses before the cutoff.
        # We target only OFFERED because those are the ones used for the per-validator
        # dedup that prevented re-offering the same submission to the same validator.
        # Other statuses (e.g. feedback from the validator) are left untouched.
        conn.execute(
            text(
                "DELETE FROM miner_submission_statuses "
                "WHERE solution_status = 'OFFERED' "
                f"AND created_at < '{CUTOFF_ISO}'"
            )
        )
        conn.commit()


def downgrade(engine):
    """Downgrade is not supported (we intentionally discard the old OFFERED markers)."""
    # Intentionally a no-op. Re-inserting the deleted OFFERED rows would require
    # knowledge of which (validator, tx, milestone) tuples had been offered before
    # the cutoff, which we do not preserve.
    pass
