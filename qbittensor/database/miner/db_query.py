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

import bittensor as bt

from ..base_query import BaseDBQuery
from .db_models import MinerSubmission, MinerSubmissionStatus


class DBQueryMiner(BaseDBQuery):
    """
    SQLAlchemy-backed query layer for miner-side submissions and status tracking.

    Phase 1 refactor: inherits from BaseDBQuery to remove repetitive
    session management boilerplate.
    """

    def __init__(self, session_factory):
        super().__init__(session_factory)

    def insert_miner_submission(
        self,
        upload_id: str,
        challenge_milestone_id: str,
        miner_hotkey: str,
        tx_hash: str,
        *,
        challenge_id: str,
        transfer_block_hash: str,
        transfer_from_ss58: str,
        transfer_to_ss58: str,
        transfer_amount_rao: str,
    ):
        """Insert or update a miner submission keyed by challenge milestone."""
        try:
            with self._managed_session() as session:
                existing_submission = (
                    session.query(MinerSubmission).filter_by(tx_hash=tx_hash).first()
                )

                if existing_submission:
                    bt.logging.info(f"Updating existing miner submission for tx_hash: {tx_hash}")
                    existing_submission.upload_id = upload_id
                    existing_submission.miner_hotkey = miner_hotkey
                    existing_submission.challenge_id = challenge_id
                    existing_submission.challenge_milestone_id = challenge_milestone_id
                    existing_submission.transfer_block_hash = transfer_block_hash
                    existing_submission.transfer_from_ss58 = transfer_from_ss58
                    existing_submission.transfer_to_ss58 = transfer_to_ss58
                    existing_submission.transfer_amount_rao = transfer_amount_rao
                    existing_submission.updated_at = datetime.now(timezone.utc)
                    bt.logging.info(f" ✅ Updated existing miner submission for tx_hash: {tx_hash}")
                    return True
                else:
                    bt.logging.info(f"Inserting new miner submission for tx_hash: {tx_hash}")
                    new_submission = MinerSubmission(
                        upload_id=upload_id,
                        challenge_id=challenge_id,
                        challenge_milestone_id=challenge_milestone_id,
                        miner_hotkey=miner_hotkey,
                        tx_hash=tx_hash,
                        transfer_block_hash=transfer_block_hash,
                        transfer_from_ss58=transfer_from_ss58,
                        transfer_to_ss58=transfer_to_ss58,
                        transfer_amount_rao=transfer_amount_rao,
                    )
                    session.add(new_submission)
                    bt.logging.info(f" ✅ Inserted new miner submission for tx_hash: {tx_hash}")
                    return True
        except Exception as e:
            bt.logging.error(f" ❌ Error inserting miner submission: {e}")
            return False

    def record_solution_served_to_validator(self, tx_hash: str) -> bool:
        """Set submitted_at when this row is handed to a validator in forward()."""
        try:
            with self._managed_session() as session:
                row = session.query(MinerSubmission).filter_by(tx_hash=tx_hash).first()
                if row is None:
                    bt.logging.error(
                        f"Cannot record submitted_at: no row for tx_hash {tx_hash}"
                    )
                    return False
                row.submitted_at = datetime.now(timezone.utc)
                return True
        except Exception as e:
            bt.logging.error(f"Error recording submitted_at for {tx_hash}: {e}")
            return False

    def get_next_miner_submission(self) -> MinerSubmission | None:
        """Return a submission to serve to any validator (no per-validator filtering)."""
        try:
            with self._managed_session(read_only=True) as session:
                return (
                    session.query(MinerSubmission)
                    .order_by(
                        MinerSubmission.submitted_at.asc().nullsfirst(),
                        MinerSubmission.updated_at.desc(),
                    )
                    .first()
                )
        except Exception as e:
            bt.logging.error(f"Error getting next miner submission: {e}")
            return None

    def insert_miner_submission_status(self, challenge_milestone_id: str, solution_status: str, validator_hotkey: str, tx_hash: str) -> bool:
        """Insert a new miner submission status record into the database."""
        try:
            with self._managed_session() as session:
                existing_status = session.query(MinerSubmissionStatus).filter_by(
                    validator_hotkey=validator_hotkey,
                    tx_hash=tx_hash,
                    challenge_milestone_id=challenge_milestone_id
                ).first()
                if existing_status:
                    bt.logging.info(f"Updating existing miner submission status for tx_hash: {tx_hash}")
                    existing_status.solution_status = solution_status
                    existing_status.updated_at = datetime.now(timezone.utc)
                    bt.logging.info(f" ✅ Updated existing miner submission status for tx_hash: {tx_hash}")
                    return True
                else:
                    bt.logging.info(f"Inserting new miner submission status for tx_hash: {tx_hash}")
                    new_status = MinerSubmissionStatus(
                        challenge_milestone_id=challenge_milestone_id,
                        solution_status=solution_status,
                        validator_hotkey=validator_hotkey,
                        tx_hash=tx_hash
                    )
                    session.add(new_status)
                    bt.logging.info(f" ✅ Inserted new miner submission status for tx_hash: {tx_hash}")
                    return True
        except Exception as e:
            bt.logging.error(f" ❌ Error inserting miner submission status: {e}")
            return False
