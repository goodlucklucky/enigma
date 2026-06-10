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
Migration 0005: Clear cached verification failures (success=0) from verified_tx_hashes.

Context:
- The verified_tx_hashes table caches results of transfer proof / fee tx verification
  (both successes and failures) to short-circuit expensive on-chain lookups.
- Previously, many "failure" records (success=0) were caused by "State discarded"
  errors when using non-archive (lite) subtensor nodes for old blocks. The actual
  on-chain data was fine; the lookup just couldn't reach it.
- We have since added:
  - Automatic fallback to the official Bittensor archive node
    (wss://archive.chain.opentensor.ai:443, overridable via ENIGMA_ARCHIVE_ENDPOINT)
  - Subscan public indexer API fallback (https://bittensor.api.subscan.io)
  - Improved get_block + parsing fallbacks

Purpose of this migration:
- Delete all success=0 (failure) rows.
- This is the easiest one-time way to give those txs a fresh verification attempt
  the next time they are seen (normal synapse processing or cross-check).
- The new verification path (with archive + Subscan) will now succeed for txs
  that were previously only failing due to lookup problems.
- Legitimate failures (bad proof, wrong amount, tampered data, etc.) will simply
  be re-detected and re-cached as failures on next encounter.
- Success=True records are left untouched (they represent real prior successful
  verifications or the 0004 backfill).

This is a data-only cleanup migration (no schema change). It is safe to re-run.
"""

from sqlalchemy import text

VERSION = 5
DESCRIPTION = "Clear cached verification failures so they can be re-verified with archive/Subscan support"


def upgrade(engine, telemetry_service=None):
    """Delete all previously-cached failure records (success=0).

    Failures are deleted rather than force-updated to success=1 because:
    - We cannot know from the old record whether it was a real failure or a lookup failure.
    - Deleting lets the live verification code (now with archive + Subscan fallbacks)
      make the correct determination on the next encounter.
    - This is the simplest and safest one-time migration.
    """
    with engine.connect() as conn:
        # Only act if the table exists (defensive, like 0002/0003/0004).
        table_check = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='verified_tx_hashes'")
        )
        if not table_check.fetchone():
            # Table doesn't exist yet (fresh DB) — nothing to clean.
            return

        # Delete all cached failures.
        # This forces a re-verification (using the improved archive/Subscan paths)
        # the next time the tx_hash is presented in a synapse or cross-check submission.
        conn.execute(text("DELETE FROM verified_tx_hashes WHERE success = 0"))
        conn.commit()


def downgrade(engine):
    """Downgrade is not supported (we intentionally discard the old failure cache)."""
    # Intentionally a no-op. We don't want to re-insert stale failure records
    # that may have been caused by the previous lack of archive/indexer support.
    pass
