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
Migration 0004: Add 'verified_tx_hashes' table.

This table acts as a local per-validator cache of transfer proof / fee tx verification results
(both successes and failures). It is populated during normal synapse processing ("up front"
verification when a submission is first received).

Purpose:
- Allow cross-checks to short-circuit expensive on-chain historical lookups
  (retrieve_extrinsic_by_hash) when this validator previously saw the same tx_hash
  during the initial submission flood.
- Record failures so that bad txs don't cause repeated noisy verification attempts.
- Reduce dependence on archive nodes for repeated verifications of the same tx.

The table is strictly a local cache. It does not replace platform-side status or the
maintenance incentive records (the latter are still only created for successful payments).

Backfill:
- All existing successful tx_hashes from miner_maintenance_incentives and challenge_solutions
  are inserted as success=True. This gives immediate benefit for past work without requiring
  re-verification.
- Historical failures are not backfilled (we never persisted them before).
"""

from sqlalchemy import text

VERSION = 4
DESCRIPTION = "Add verified_tx_hashes table (local verification cache for tx proofs)"


def upgrade(engine, telemetry_service=None):
    """Create the table (if missing) and backfill known successful tx_hashes."""
    with engine.connect() as conn:
        # Idempotent table creation using raw SQL for SQLite compatibility and safety.
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS verified_tx_hashes (
                tx_hash TEXT PRIMARY KEY,
                success INTEGER NOT NULL,
                error_message TEXT,
                verified_at DATETIME DEFAULT (datetime('now')),
                miner_hotkey TEXT
            )
        """))
        conn.commit()

        # Backfill only if the source tables exist at migration time.
        # Migrations run *before* create_all in DBConnection, so on brand-new DBs
        # the source tables (challenge_solutions, miner_maintenance_incentives) do
        # not exist yet — create_all will create them empty afterward. In that case
        # there is no historical data to backfill, which is the correct behavior.
        # On existing DBs the tables are present, so we copy prior successful txs.
        # We use INSERT OR IGNORE so the migration is safe to re-run.

        # Check source tables (pattern used by 0002/0003 for the same reason).
        has_incentives = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='miner_maintenance_incentives'")
        ).fetchone()

        if has_incentives:
            # miner_maintenance_incentives has the authoritative list of successful fee payments.
            conn.execute(text("""
                INSERT OR IGNORE INTO verified_tx_hashes (tx_hash, success, error_message, miner_hotkey, verified_at)
                SELECT tx_hash, 1, NULL, miner_hotkey, datetime('now')
                FROM miner_maintenance_incentives
            """))

        has_solutions = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='challenge_solutions'")
        ).fetchone()

        if has_solutions:
            # Also backfill from challenge_solutions (in case some rows exist without a matching incentive row).
            conn.execute(text("""
                INSERT OR IGNORE INTO verified_tx_hashes (tx_hash, success, error_message, miner_hotkey, verified_at)
                SELECT tx_hash, 1, NULL, miner_hotkey, datetime('now')
                FROM challenge_solutions
                WHERE tx_hash IS NOT NULL
            """))

        conn.commit()


def downgrade(engine):
    """Downgrade is not supported (we keep the history table for audit value)."""
    # Intentionally a no-op, matching the style of prior migrations.
    # Dropping the table would lose any cached verification history.
    pass
