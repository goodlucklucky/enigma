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

import uuid

import bittensor as bt
from sqlalchemy import func
from sqlalchemy.dialects.sqlite import insert

from ..base_query import BaseDBQuery
from .db_models import ChallengeSolution, MinerMaintenanceIncentive, VerifiedTxHash
from qbittensor.protocol import MinerSubmissionStatus
from qbittensor.utils.solution_status import SolutionStatus

# SQLite datetime() does not support "weeks" modifier; use equivalent day count.
OLDEST_ALLOWED_TIMESTAMP = "-21 days"  # Used to prune solutions that are older than 3 weeks.


class DBQuery(BaseDBQuery):
    """
    SQLAlchemy-backed query layer for validator-side challenge solution
    and maintenance incentive data.

    Phase 1 refactor: inherits from BaseDBQuery to eliminate the repetitive
    session management boilerplate that was present in nearly every method.
    """

    def __init__(self, session_factory):
        super().__init__(session_factory)

    def _pending_placeholder(self, tx_hash: str, field: str) -> str:
        return f"pending:{field}:{tx_hash}"

    def create_challenge_solution(
        self,
        challenge_validation_solution_id: str,
        challenge_milestone_id: str,
        submission_id: str | None,
        solution_status: str,
        tx_hash: str,
        miner_hotkey: str,
        challenge_id: str | None = None,
        max_solution_runtime_seconds: int | None = None,
    ) -> str | None:
        """Create (or re-use via guarded upsert) a challenge solution row for a tx_hash
        before the container/runtime fields are known.

        Returns the stable solution `id` (PK) on success (new or re-used row), or None on failure.
        This lets callers (e.g. the execution path) immediately get the id for docker labels
        and by-id updates without a separate get-by-tx lookup.

        Re-execution / re-run (including when the cloud offers a cross-check for
        an already-processed item) is permitted only when the key identifiers for
        the work item match those already associated with this `tx_hash`:
        file upload (via challenge_validation_solution_id / upload_endpoint_id),
        challenge_id, and challenge_milestone_id (submission_id is also checked
        for the primary cloud claim binding).

        If the cloud offers the same tx_hash but with a differing file-upload /
        challenge_id / milestone_id (or a conflicting submission_id for different
        work), the request is rejected. This disallows tx_hash reuse across
        different submissions/uploads/challenges/milestones.
        """
        return self.insert_challenge_solution(
            challenge_validation_solution_id=challenge_validation_solution_id,
            container_id=self._pending_placeholder(tx_hash, "container_id"),
            container_name=self._pending_placeholder(tx_hash, "container_name"),
            image_id=self._pending_placeholder(tx_hash, "image_id"),
            challenge_id=challenge_id,
            challenge_milestone_id=challenge_milestone_id,
            absolute_path_to_solution=self._pending_placeholder(tx_hash, "path"),
            submission_id=submission_id or "",
            solution_status=solution_status,
            tx_hash=tx_hash,
            miner_hotkey=miner_hotkey,
            cleaned=False,
            max_solution_runtime_seconds=max_solution_runtime_seconds,
        )

    def update_challenge_solution_status_by_id(self, solution_id: str, solution_status: str) -> bool:
        """Update only the solution status using the stable primary key id.

        Preferred (delegates to the general by-id updater). Use this (or the
        shorter update_solution_status_by_id) and carry the id from
        create_challenge_solution / SolutionPostProcessInfo (which embeds SolutionExecution) for all status updates.
        """
        return self.update_solution_status_by_id(solution_id, solution_status)

    def update_challenge_solution_by_id(
        self,
        solution_id: str,
        container_id: str,
        container_name: str,
        image_id: str,
        absolute_path_to_solution: str,
        solution_status: str,
    ) -> bool:
        """Update runtime fields (container, image, path, status) using the stable primary key id.

        This is the clean path once the solution row has been created and its id
        is available (returned by create_challenge_solution, or via labels -> get_by_id).
        """
        try:
            with self._managed_session() as session:
                solution = session.query(ChallengeSolution).filter_by(id=solution_id).first()
                if not solution:
                    bt.logging.warning(f"No challenge solution found with id: {solution_id}")
                    return False
                solution.container_id = container_id
                solution.container_name = container_name
                solution.image_id = image_id
                solution.absolute_path_to_solution = absolute_path_to_solution
                solution.solution_status = solution_status
                bt.logging.info(f" ✅ Updated challenge solution id={solution_id}")
                return True
        except Exception as e:
            bt.logging.error(f" ❌ Error updating challenge solution by id: {e}")
            return False

    def insert_challenge_solution(
        self,
        challenge_validation_solution_id,
        container_id,
        container_name,
        image_id,
        challenge_milestone_id,
        absolute_path_to_solution,
        submission_id,
        solution_status,
        tx_hash,
        miner_hotkey,
        challenge_id: str | None = None,
        cleaned: bool = False,
        max_solution_runtime_seconds: int | None = None,
    ):
        """Insert (or upsert on tx_hash) a challenge solution record.

        Re-execution of the same cloud submission, or re-running for a cross-check
        that the cloud offers for the *same* file upload / tx_hash / challenge id /
        milestone id, is allowed when the identifiers are consistent with the
        row already bound to this tx_hash.

        The primary binding is tx_hash → one cloud submission (by submission_id),
        plus the specific work item (file upload identified by
        challenge_validation_solution_id/upload_endpoint_id, plus challenge_id and
        challenge_milestone_id).

        If an existing row for the tx_hash has differing values for any of
        submission_id (for different work), challenge_validation_solution_id (file
        upload), challenge_id, or challenge_milestone_id, the upsert is rejected.
        This disallows tx_hash reuse for different submissions / file uploads /
        challenges / milestones.
        """
        bt.logging.info(f"Inserting/Upserting challenge solution with tx_hash: {tx_hash}")
        try:
            with self._managed_session() as session:
                existing = session.query(ChallengeSolution).filter_by(tx_hash=tx_hash).first()

                if existing:
                    # Check consistency of the key identifiers the cloud uses to identify
                    # a cross-check / execution item for this tx: file upload (the
                    # challenge_validation_solution_id / upload_endpoint_id), challenge_id,
                    # challenge_milestone_id, and submission_id.
                    # This allows legitimate re-runs when the cloud re-offers the *same*
                    # cross-check for the same file/tx/challenge/milestone (even if it
                    # presents a different submission.id for the cross-check task itself),
                    # while rejecting attempts to reuse a tx_hash for different work.
                    mismatches = []
                    if existing.submission_id and submission_id and existing.submission_id != submission_id:
                        mismatches.append(
                            f"submission_id (existing={existing.submission_id} != incoming={submission_id})"
                        )
                    if (
                        existing.challenge_validation_solution_id
                        and challenge_validation_solution_id
                        and existing.challenge_validation_solution_id != challenge_validation_solution_id
                    ):
                        mismatches.append(
                            f"challenge_validation_solution_id/file_upload "
                            f"(existing={existing.challenge_validation_solution_id} != incoming={challenge_validation_solution_id})"
                        )
                    if existing.challenge_id and challenge_id and existing.challenge_id != challenge_id:
                        mismatches.append(
                            f"challenge_id (existing={existing.challenge_id} != incoming={challenge_id})"
                        )
                    if existing.challenge_milestone_id != challenge_milestone_id:
                        mismatches.append(
                            f"challenge_milestone_id (existing={existing.challenge_milestone_id} != incoming={challenge_milestone_id})"
                        )

                    if mismatches:
                        # Special case: allow if this is a cross-check / re-run for the
                        # *same* underlying file upload + challenge + milestone, even if
                        # the submission_id differs (the cross-check task uses its own id).
                        # We consider the work the same if file/ch/milestone line up.
                        work_id_mismatches = [m for m in mismatches if "submission_id" not in m]
                        if work_id_mismatches:
                            # Real mismatch on the work item (file / ch / mil) → hard reject to
                            # disallow tx reuse for different submissions/uploads/challenges.
                            bt.logging.error(
                                f"❌ Refusing upsert for tx_hash={tx_hash}: "
                                f"identifier mismatch on {', '.join(mismatches)}. "
                                "A tx_hash must not be reused for a different file upload / "
                                "submission / challenge / milestone."
                            )
                            return None
                        else:
                            # Only submission_id differs, but file/challenge/milestone match
                            # the previously recorded work for this tx → this is a legitimate
                            # re-execution (e.g. platform cross-check for the same miner's
                            # uploaded solution).
                            bt.logging.info(
                                f"Re-executing same work item for tx_hash={tx_hash} "
                                f"(file/ch/milestone match; submission_id differs, e.g. cross-check "
                                f"existing={existing.submission_id} incoming={submission_id})"
                            )
                    else:
                        # Everything (including submission_id) matches → standard re-execution
                        # of the same cloud submission.
                        bt.logging.info(
                            f"Re-executing same cloud submission (tx_hash={tx_hash}, "
                            f"submission_id={submission_id})"
                        )

                now = func.now()
                stmt = insert(ChallengeSolution).values(
                    id=str(uuid.uuid4()),  # only used on actual INSERT
                    challenge_validation_solution_id=challenge_validation_solution_id,
                    container_id=container_id,
                    container_name=container_name,
                    image_id=image_id,
                    challenge_id=challenge_id,
                    challenge_milestone_id=challenge_milestone_id,
                    absolute_path_to_solution=absolute_path_to_solution,
                    submission_id=submission_id,
                    solution_status=solution_status,
                    tx_hash=tx_hash,
                    miner_hotkey=miner_hotkey,
                    cleaned=cleaned,
                    max_solution_runtime_seconds=max_solution_runtime_seconds,
                    created_at=now,
                    updated_at=now,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["tx_hash"],
                    set_={
                        "challenge_validation_solution_id": challenge_validation_solution_id,
                        "container_id": container_id,
                        "container_name": container_name,
                        "image_id": image_id,
                        "challenge_id": challenge_id,
                        "challenge_milestone_id": challenge_milestone_id,
                        "absolute_path_to_solution": absolute_path_to_solution,
                        "submission_id": submission_id,
                        "solution_status": solution_status,
                        "miner_hotkey": miner_hotkey,
                        "cleaned": cleaned,
                        "max_solution_runtime_seconds": max_solution_runtime_seconds,
                        "updated_at": now,
                    },
                )
                session.execute(stmt)

                # Return the authoritative id (the PK that was inserted or already present on re-use).
                # Callers get the id directly from create/insert; no need for a follow-up tx_hash lookup.
                row = session.query(ChallengeSolution).filter_by(tx_hash=tx_hash).first()
                if row:
                    action = "Re-used" if existing else "Inserted new"
                    bt.logging.info(
                        f" ✅ {action} challenge solution row "
                        f"(id={row.id}, submission_id={submission_id}, tx_hash={tx_hash})"
                    )
                    return row.id

                return None

        except Exception as e:
            bt.logging.error(f" ❌ Error inserting/updating challenge solution: {e}")
            return None

    def update_solution_status_by_id(self, solution_id: str, solution_status: str) -> bool:
        """Update the solution status using the stable primary key id from the DB row.

        This is the cleanest once you have the id in a passed-around object
        (e.g. SolutionPostProcessInfo.id or .execution.solution_id).
        """
        try:
            with self._managed_session() as session:
                solution = session.query(ChallengeSolution).filter_by(id=solution_id).first()
                if solution:
                    solution.solution_status = solution_status
                    bt.logging.info(f"Updated solution status for id {solution_id} to {solution_status}")
                    return True
                else:
                    bt.logging.warning(f"No challenge solution found with id: {solution_id}")
                    return False
        except Exception as e:
            bt.logging.error(f"Error updating solution status by id: {e}")
            return False

    def prune_old_solutions(self):
        """Prune old solutions from the database."""
        try:
            with self._managed_session() as session:
                deleted = session.query(ChallengeSolution).filter(
                    ChallengeSolution.updated_at < func.datetime('now', OLDEST_ALLOWED_TIMESTAMP)
                ).delete()
                bt.logging.info(f"🗑️ Pruned {deleted} old solutions from the database (older than {OLDEST_ALLOWED_TIMESTAMP})")
                return True
        except Exception as e:
            bt.logging.error(f"Error pruning old solutions: {e}")
            return False

    def get_challenge_solution_by_id(self, solution_id: str):
        """Retrieve a challenge solution record by its primary key (the stable DB id).

        Preferred for any lookups once you have the id (e.g. from a passed-around
        SolutionPostProcessInfo (embedding SolutionExecution) or other context object).
        """
        try:
            with self._managed_session(read_only=True) as session:
                solution = session.query(ChallengeSolution).filter_by(id=solution_id).first()
                if solution:
                    bt.logging.info(f"Retrieved challenge solution with id: {solution_id}")
                    return solution
                else:
                    bt.logging.warning(f"No challenge solution found with id: {solution_id}")
                    return None
        except Exception as e:
            bt.logging.error(f"Error retrieving challenge solution by id: {e}")
            return None

    def mark_solution_cleaned(self, solution_id: str) -> bool:
        """Mark a solution record as cleaned (FS/containers removed)."""
        try:
            with self._managed_session() as session:
                solution = session.query(ChallengeSolution).filter_by(id=solution_id).first()
                if solution:
                    solution.cleaned = True
                    bt.logging.info(f"Marked solution {solution_id} as cleaned")
                    return True
                else:
                    bt.logging.warning(f"No challenge solution found with id: {solution_id} to mark cleaned")
                    return False
        except Exception as e:
            bt.logging.error(f"Error marking solution cleaned: {e}")
            return False

    def get_uncleaned_solutions(self):
        """Get all ChallengeSolution records that have not been marked cleaned."""
        try:
            with self._managed_session(read_only=True) as session:
                return (
                    session.query(ChallengeSolution)
                    .filter_by(cleaned=False)
                    .order_by(ChallengeSolution.created_at.desc())
                    .all()
                )
        except Exception as e:
            bt.logging.error(f"Error querying uncleaned solutions: {e}")
            return []

    def get_running_solutions(self):
        """Get ChallengeSolution records that are expected to be running (status=RUNNING, not cleaned).
        These are the ones the watchdog should be monitoring for completion or timeout.
        """
        try:
            with self._managed_session(read_only=True) as session:
                return (
                    session.query(ChallengeSolution)
                    .filter(
                        ChallengeSolution.solution_status == SolutionStatus.RUNNING.value,
                        ChallengeSolution.cleaned.is_(False),
                    )
                    .order_by(ChallengeSolution.created_at.desc())
                    .all()
                )
        except Exception as e:
            bt.logging.error(f"Error querying running solutions: {e}")
            return []

    def get_stale_pending_or_running_solutions(self):
        """Return RUNNING (not cleaned) + PENDING (not cleaned) rows.
        Used by the watchdog reconciliation scan to catch rows whose containers
        have been cleaned up externally or via other paths, so we can force them
        to FAILED and prevent them from hanging as RUNNING forever.
        """
        results = []
        try:
            with self._managed_session(read_only=True) as session:
                running = (
                    session.query(ChallengeSolution)
                    .filter(
                        ChallengeSolution.solution_status == SolutionStatus.RUNNING.value,
                        ChallengeSolution.cleaned.is_(False),
                    )
                    .all()
                )
                results.extend(running)

                pending = (
                    session.query(ChallengeSolution)
                    .filter(
                        ChallengeSolution.solution_status == SolutionStatus.PENDING.value,
                        ChallengeSolution.cleaned.is_(False),
                    )
                    .all()
                )
                results.extend(pending)
        except Exception as e:
            bt.logging.error(f"Error querying stale/pending/running solutions: {e}")
        return results

    def remove_solution_by_id(self, solution_id: str) -> bool:
        """Remove a challenge solution record by its primary id."""
        try:
            with self._managed_session() as session:
                solution = session.query(ChallengeSolution).filter_by(id=solution_id).first()
                if solution:
                    session.delete(solution)
                    bt.logging.info(f"Removed challenge solution with id: {solution_id}")
                    return True
                else:
                    bt.logging.warning(f"No challenge solution found with id: {solution_id}")
                    return False
        except Exception as e:
            bt.logging.error(f"Error removing challenge solution by id: {e}")
            return False

    def get_miner_submission_statuses(self, miner_hotkey):
        """Retrieve the challenge solutions for a given miner hotkey from the database."""
        try:
            with self._managed_session(read_only=True) as session:
                solutions = session.query(ChallengeSolution).filter_by(miner_hotkey=miner_hotkey).all()
                if solutions:
                    bt.logging.info(f"Retrieved {len(solutions)} challenge solutions for miner hotkey: {miner_hotkey}")
                    return [
                        MinerSubmissionStatus(
                            status=solution.solution_status,
                            miner_hotkey=solution.miner_hotkey,
                            tx_hash=solution.tx_hash,
                            challenge_milestone_id=solution.challenge_milestone_id,
                        )
                        for solution in solutions
                    ]
                else:
                    return []
        except Exception as e:
            bt.logging.error(f"Error retrieving challenge solutions for miner hotkey: {miner_hotkey}: {e}")
            return []

    def insert_for_maintenance_incentive(
        self,
        miner_hotkey: str,
        challenge_milestone_id: str,
        tx_hash: str,
    ):
        """Insert a new miner maintenance incentive record."""
        try:
            with self._managed_session() as session:
                stmt = insert(MinerMaintenanceIncentive).values(
                    miner_hotkey=miner_hotkey,
                    challenge_milestone_id=challenge_milestone_id,
                    tx_hash=tx_hash,
                )
                stmt = stmt.on_conflict_do_nothing(index_elements=["tx_hash"])
                session.execute(stmt)
                bt.logging.info(f"💾 Inserted miner solution with miner_hotkey: {miner_hotkey}")
                return True
        except Exception as e:
            bt.logging.error(f"❌ Error inserting miner solution: {e}")
            return False

    def get_solution_by_submission_id(self, submission_id: str):
        """Get a ChallengeSolution by submission_id."""
        try:
            with self._managed_session(read_only=True) as session:
                solution = session.query(ChallengeSolution).filter_by(submission_id=submission_id).first()
                return solution
        except Exception as e:
            bt.logging.error(f"Error querying by submission_id {submission_id}: {e}")
            return None

    def prune_old_miner_solutions(self):
        """
        Efficiently prune old miner maintenance incentive records.

        Uses a bulk DELETE instead of loading rows and deleting individually.
        This is both faster and uses far less memory.
        """
        try:
            with self._managed_session() as session:
                cutoff_time = func.datetime('now', OLDEST_ALLOWED_TIMESTAMP)

                deleted_count = (
                    session.query(MinerMaintenanceIncentive)
                    .filter(MinerMaintenanceIncentive.updated_at < cutoff_time)
                    .delete(synchronize_session=False)
                )

                bt.logging.info(
                    f"🗑️ Pruned {deleted_count} old miner maintenance incentive rows "
                    f"(older than {OLDEST_ALLOWED_TIMESTAMP})"
                )
                return True
        except Exception as e:
            bt.logging.error(f"Error pruning old miner solutions: {e}")
            return False

    def _get_active_maintenance_hotkeys(self, cutoff_time) -> list[str]:
        """
        Internal helper: return distinct miner hotkeys that have recent
        maintenance incentive records (used for weight setting).
        """
        with self._managed_session(read_only=True) as session:
            rows = (
                session.query(MinerMaintenanceIncentive.miner_hotkey)
                .filter(MinerMaintenanceIncentive.updated_at >= cutoff_time)
                .distinct()
                .all()
            )
            return [row[0] for row in rows]

    def get_active_miners(self) -> list[str]:
        """Get a list of active miners that should receive maintenance incentives."""
        try:
            cutoff_time = func.datetime('now', OLDEST_ALLOWED_TIMESTAMP)
            active_miners = self._get_active_maintenance_hotkeys(cutoff_time)

            bt.logging.debug(f"Retrieved {len(active_miners)} active miners for maintenance incentives")

            return active_miners
        except Exception as e:
            bt.logging.error(f"Error retrieving active miners: {e}")
            return []

    def has_seen_tx_hash(self, tx_hash: str) -> bool:
        """
        Returns True if this validator has already processed this fee transaction.

        We check the maintenance incentive table, the full solutions table,
        and the verified_tx_hashes cache. This is intentionally cheap indexed lookups.

        Note: The verified cache entry alone typically indicates a prior verification
        (success or failure) without a local work-item binding. Bindings that establish
        tx <-> specific file upload / challenge / milestone uniqueness live in the
        challenge_solutions (and incentive) rows; see get_tx_binding_info.
        """
        try:
            with self._managed_session(read_only=True) as session:
                # Fast path: maintenance incentives
                if session.query(MinerMaintenanceIncentive).filter_by(tx_hash=tx_hash).first() is not None:
                    return True

                # Fallback: check actual processed solutions
                if session.query(ChallengeSolution).filter_by(tx_hash=tx_hash).first() is not None:
                    return True

                # Also consider prior verification attempts (success or failure) in the new cache
                if session.query(VerifiedTxHash).filter_by(tx_hash=tx_hash).first() is not None:
                    return True

                return False
        except Exception:
            # Conservative: if we can't check, assume we haven't seen it
            # (upstream code will handle duplicates safely via on_conflict_do_nothing)
            bt.logging.warning(f"Error checking has_seen_tx_hash for {tx_hash}, assuming not seen")
            return False

    def get_tx_binding_info(self, tx_hash: str) -> dict | None:
        """
        Return the work-item binding information for a tx_hash if a ChallengeSolution row
        exists for it. This is the local record that ties a specific payment (tx) to a
        specific file upload (challenge_validation_solution_id / upload_endpoint_id) plus
        challenge and milestone.

        Used to detect attempts to reuse the same tx for a *different* file upload / work
        item even after adding the verified_tx_hashes verification cache (which is tx-only
        and does not store upload identifiers).

        Returns None if there is no local solution row for the tx (e.g. the tx was only
        seen for a failed verification, or never successfully bound on this validator).
        """
        try:
            with self._managed_session(read_only=True) as session:
                row = session.query(ChallengeSolution).filter_by(tx_hash=tx_hash).first()
                if not row:
                    return None
                return {
                    "challenge_validation_solution_id": row.challenge_validation_solution_id,
                    "challenge_id": row.challenge_id,
                    "challenge_milestone_id": row.challenge_milestone_id,
                    "submission_id": row.submission_id,
                }
        except Exception:
            bt.logging.warning(f"Error fetching tx binding info for {tx_hash}")
            return None

    def get_verified_tx_result(self, tx_hash: str) -> tuple[bool | None, str | None]:
        """
        Returns (success, error_message) if this tx_hash was previously verified by this
        validator (via the verified_tx_hashes cache). Returns (None, None) if never verified.
        Used to short-circuit re-verification on cross-checks.
        """
        try:
            with self._managed_session(read_only=True) as session:
                row = session.query(VerifiedTxHash).filter_by(tx_hash=tx_hash).first()
                if row:
                    return bool(row.success), row.error_message
                return None, None
        except Exception as e:
            bt.logging.error(f"Error getting verified tx result for {tx_hash}: {e}")
            return None, None

    def record_verified_tx(self, tx_hash: str, success: bool, error_message: str | None = None, miner_hotkey: str | None = None):
        """Insert or update the verification result for a tx_hash (cache for future cross-checks)."""
        try:
            with self._managed_session() as session:
                stmt = insert(VerifiedTxHash).values(
                    tx_hash=tx_hash,
                    success=success,
                    error_message=error_message,
                    miner_hotkey=miner_hotkey,
                    verified_at=func.now(),
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["tx_hash"],
                    set_={
                        "success": success,
                        "error_message": error_message,
                        "miner_hotkey": miner_hotkey,
                        "verified_at": func.now(),
                    },
                )
                session.execute(stmt)
        except Exception as e:
            bt.logging.error(f"Error recording verified tx {tx_hash}: {e}")
