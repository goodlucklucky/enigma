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
import uuid

import bittensor as bt
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..base_query import BaseDBQuery
from .db_models import MinerSubmission, MinerSubmissionStatus

# Canonical status values stored in miner_submission_statuses.solution_status
# (kept in ALL CAPS for consistency with SolutionStatus on the validator side)
MINER_SUBMISSION_STATUS_OFFERED = "OFFERED"


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
        """
        Upsert a miner submission status record.

        Uses SQLite ON CONFLICT DO UPDATE so that if a row already exists for the
        (validator_hotkey, tx_hash, challenge_milestone_id) tuple (e.g. we previously
        recorded "OFFERED"), we atomically update the status instead of attempting
        a duplicate INSERT (which can fail the tx_hash FK or create duplicates).
        """
        try:
            with self._managed_session() as session:
                now = datetime.now(timezone.utc)
                stmt = sqlite_insert(MinerSubmissionStatus).values(
                    id=str(uuid.uuid4()),
                    challenge_milestone_id=challenge_milestone_id,
                    solution_status=solution_status,
                    validator_hotkey=validator_hotkey,
                    tx_hash=tx_hash,
                    created_at=now,
                    updated_at=now,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["validator_hotkey", "tx_hash", "challenge_milestone_id"],
                    set_={
                        "solution_status": solution_status,
                        "updated_at": now,
                    },
                )
                session.execute(stmt)
                bt.logging.info(
                    f"✅ Upserted miner submission status '{solution_status}' for tx_hash: {tx_hash}"
                )
                return True
        except Exception as e:
            bt.logging.error(f"❌ Error upserting miner submission status: {e}")
            return False

    def record_submission_offered_to_validator(
        self, tx_hash: str, validator_hotkey: str, challenge_milestone_id: str
    ) -> bool:
        """
        Record that we have offered (served) this submission to a specific validator.
        This creates an 'OFFERED' status entry so we can avoid re-offering the same
        submission to the same validator in the future.
        """
        return self.insert_miner_submission_status(
            challenge_milestone_id=challenge_milestone_id,
            solution_status=MINER_SUBMISSION_STATUS_OFFERED,
            validator_hotkey=validator_hotkey,
            tx_hash=tx_hash,
        )

    def get_next_miner_submission_for_validator(self, validator_hotkey: str) -> MinerSubmission | None:
        """
        Return the next submission that has never been offered to this specific validator yet.
        This enables proper per-validator deduplication of offerings.
        """
        try:
            with self._managed_session(read_only=True) as session:
                # Subquery of tx_hashes this validator has already seen (any status)
                seen_subq = (
                    session.query(MinerSubmissionStatus.tx_hash)
                    .filter(MinerSubmissionStatus.validator_hotkey == validator_hotkey)
                    .subquery()
                )

                return (
                    session.query(MinerSubmission)
                    .filter(~MinerSubmission.tx_hash.in_(seen_subq))
                    .order_by(
                        MinerSubmission.submitted_at.asc().nullsfirst(),
                        MinerSubmission.updated_at.desc(),
                    )
                    .first()
                )
        except Exception as e:
            bt.logging.error(f"Error getting next miner submission for validator {validator_hotkey}: {e}")
            return None

    def list_my_submissions_with_status(self, limit: int = 50) -> list[dict]:
        """
        Return recent submissions along with their latest known status per validator.
        Useful for CLI listing and operator visibility.
        """
        try:
            with self._managed_session(read_only=True) as session:
                # Get submissions
                subs = (
                    session.query(MinerSubmission)
                    .order_by(MinerSubmission.created_at.desc())
                    .limit(limit)
                    .all()
                )

                results = []
                for sub in subs:
                    # Get all statuses for this submission
                    statuses = (
                        session.query(MinerSubmissionStatus)
                        .filter_by(tx_hash=sub.tx_hash)
                        .order_by(MinerSubmissionStatus.updated_at.desc())
                        .all()
                    )

                    status_summary = {}
                    for st in statuses:
                        if st.validator_hotkey not in status_summary:
                            status_summary[st.validator_hotkey] = {
                                "status": st.solution_status,
                                "updated_at": st.updated_at,
                            }

                    results.append({
                        "tx_hash": sub.tx_hash,
                        "challenge_milestone_id": sub.challenge_milestone_id,
                        "upload_id": sub.upload_id,
                        "created_at": sub.created_at,
                        "submitted_at": sub.submitted_at,
                        "validator_statuses": status_summary,
                    })

                return results
        except Exception as e:
            bt.logging.error(f"Error listing miner submissions with status: {e}")
            return []
