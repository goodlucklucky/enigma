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

from datetime import datetime, timezone

import pytest

from qbittensor.database.db_connection import DBConnection


@pytest.fixture
def miner_query(miner_db):
    return miner_db.db_query_miner


class TestDBQueryMiner:
    def _insert(self, query, tx_hash, miner_db, *, submitted_at=None, updated_at=None):
        ok = query.insert_miner_submission(
            upload_id="up-1",
            challenge_milestone_id="milestone-1",
            miner_hotkey="5MinerHotkey",
            tx_hash=tx_hash,
            challenge_id="ch-1",
            transfer_block_hash="block",
            transfer_from_ss58="5From",
            transfer_to_ss58="5To",
            transfer_amount_rao="1000",
        )
        assert ok is True
        if submitted_at is not None or updated_at is not None:
            session = miner_db.get_db_session()
            try:
                from qbittensor.database.miner.db_models import MinerSubmission
                row = session.query(MinerSubmission).filter_by(tx_hash=tx_hash).one()
                if submitted_at is not None:
                    row.submitted_at = submitted_at
                if updated_at is not None:
                    row.updated_at = updated_at
                session.commit()
            finally:
                session.close()

    def test_insert_and_update_by_tx_hash(self, miner_query, miner_db):
        self._insert(miner_query, "0xfirst", miner_db)
        ok = miner_query.insert_miner_submission(
            upload_id="up-2",
            challenge_milestone_id="milestone-2",
            miner_hotkey="5MinerHotkey",
            tx_hash="0xfirst",
            challenge_id="ch-1",
            transfer_block_hash="block2",
            transfer_from_ss58="5From",
            transfer_to_ss58="5To",
            transfer_amount_rao="2000",
        )
        assert ok is True
        nxt = miner_query.get_next_miner_submission()
        assert nxt.tx_hash == "0xfirst"
        assert nxt.upload_id == "up-2"

    def test_get_next_prefers_unsubmitted(self, miner_query, miner_db):
        self._insert(miner_query, "0xold", miner_db, submitted_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
        self._insert(miner_query, "0xnew", miner_db)
        nxt = miner_query.get_next_miner_submission()
        assert nxt.tx_hash == "0xnew"

    def test_record_solution_served_to_validator(self, miner_query, miner_db):
        self._insert(miner_query, "0xserve", miner_db)
        assert miner_query.record_solution_served_to_validator("0xserve") is True
        nxt = miner_query.get_next_miner_submission()
        assert nxt.submitted_at is not None

    def test_record_solution_served_missing_tx_returns_false(self, miner_query):
        assert miner_query.record_solution_served_to_validator("0xmissing") is False

    def test_insert_miner_submission_status_insert_and_update(self, miner_query, miner_db):
        self._insert(miner_query, "0xstatus", miner_db)
        assert miner_query.insert_miner_submission_status(
            "m1", "RUNNING", "5Validator", "0xstatus"
        ) is True
        assert miner_query.insert_miner_submission_status(
            "m1", "SUCCESS", "5Validator", "0xstatus"
        ) is True

    def test_insert_miner_submission_status_unknown_tx_returns_false_no_error(self, miner_query):
        """Unknown tx_hash (e.g. stale status from validator history or cross-check) must be ignored gracefully.

        This prevents FOREIGN KEY constraint errors when validators send status updates
        (including FAILED) for submissions the miner no longer has in its local DB.
        """
        # Must return False without raising IntegrityError on the FK to miner_submissions.tx_hash
        assert miner_query.insert_miner_submission_status(
            challenge_milestone_id="milestone-x",
            solution_status="FAILED",
            validator_hotkey="5Validator",
            tx_hash="0xnonexistent-tx-that-was-never-inserted",
        ) is False
