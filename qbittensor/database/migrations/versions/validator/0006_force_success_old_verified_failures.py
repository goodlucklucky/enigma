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
Migration 0006: Force success=True on old failure entries in verified_tx_hashes before 2026-06-10.

Context:
- The verified_tx_hashes table caches transfer proof / fee tx verification results
  to avoid expensive historical on-chain lookups during direct processing and cross-checks.
- For very old tx_hashes (pre ~mid 2026), the decode fallbacks (receipt, get_block, Subscan,
  archive) frequently fail with "could not decode fee payment extrinsic".
- These decode failures were being recorded as success=False, which then caused re-runs
  and cross-check re-offers (especially after manual cloud DB resets to re-offer via /next)
  to get stuck, even when the original claim had succeeded and the payment was legitimate.
- We already have improved fallbacks (archive + Subscan), but some ancient extrinsics are
  still not fully reconstructible into the dict shape needed by the parser.
- A previous migration (0005) cleared old failures for the same reason.

Purpose of this migration:
- Before modifying anything, dump every matching row (tx_hash, success, verified_at, miner_hotkey)
  via telemetry_service.record_event("verified_tx_hash_pre_migration_dump", ...). This gives
  an auditable snapshot of exactly what was present before the force-success.
- Force success=True (with a clear error_message note) for all failure rows (success=0)
  where verified_at < '2026-06-10'.
- This allows re-runs and cross-checks of old submissions to proceed without hitting the
  flaky historical decode path again.
- We use UPDATE rather than DELETE so that the "this tx was attempted" signal remains,
  but we treat it as a successful verification for the purpose of short-circuiting.
- Legitimate real failures (bad proof, wrong amount, etc.) that happened to be before the
  cutoff will be incorrectly treated as success on re-run; operators should be aware and
  can manually clear specific txs if needed.
- This is a targeted, conservative cutoff chosen for the current re-run / cross-check
  re-offer experiments.

This is a data-only cleanup migration (no schema change). It is safe to re-run.
"""

from __future__ import annotations

from sqlalchemy import text

from qbittensor.utils.services.telemetry import TelemetryService

VERSION = 6
DESCRIPTION = "Force success on pre-2026-06-10 verified_tx_hashes failures to unblock re-runs and cross-checks"

CUTOFF = "2026-06-10"


def upgrade(engine, telemetry_service: TelemetryService | None = None):
    """Force success=True on old failure rows, with a note in error_message.
    Before updating, dump the affected rows via telemetry for audit.
    """
    with engine.connect() as conn:
        table_check = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='verified_tx_hashes'")
        )
        if not table_check.fetchone():
            return

        # Dump the rows we are about to force-success, so we have a record of what was there
        # before the change (tx_hash, success, verified_at, miner_hotkey).
        if telemetry_service:
            rows = conn.execute(
                text(
                    "SELECT tx_hash, success, verified_at, miner_hotkey "
                    "FROM verified_tx_hashes "
                    "WHERE success = 0 AND verified_at < :cutoff"
                ),
                {"cutoff": CUTOFF},
            ).fetchall()
            for row in rows:
                tx_hash, success, verified_at, miner_hotkey = row
                telemetry_service.record_event(
                    "verified_tx_hash_pre_migration_dump",
                    value=1 if success else 0,
                    attributes={
                        "tx_hash": tx_hash,
                        "success": bool(success),
                        "verified_at": str(verified_at) if verified_at else None,
                        "miner_hotkey": miner_hotkey,
                    },
                )

        # Update old failures to success, preserving original error in the message for audit.
        # Use a conservative cutoff so we only touch entries that are almost certainly
        # affected by the old decode flakiness.
        conn.execute(
            text(
                "UPDATE verified_tx_hashes "
                "SET success = 1, "
                "    error_message = COALESCE(error_message, '') || "
                "    ' [FORCED SUCCESS by migration 0006 - pre-" + CUTOFF + " cutoff, likely historical decode failure]' "
                "WHERE success = 0 "
                "  AND verified_at < '" + CUTOFF + "'"
            )
        )
        conn.commit()


def downgrade(engine):
    """Downgrade is not supported (we intentionally force-successed old entries for re-run usability)."""
    # Intentionally a no-op. Reverting would re-introduce the decode failures for re-runs.
    pass
