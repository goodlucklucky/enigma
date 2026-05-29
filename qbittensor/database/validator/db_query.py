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

import bittensor as bt
from sqlalchemy import func
from sqlalchemy.dialects.sqlite import insert

from ..base_query import BaseDBQuery
from .db_models import ChallengeSolution, MinerMaintenanceIncentive
from qbittensor.protocol import MinerSubmissionStatus

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
    ) -> bool:
        """Insert a challenge solution row before container/runtime fields are known."""
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
        )

    def update_challenge_solution_status(self, tx_hash: str, solution_status: str) -> bool:
        """Update only the solution status on an existing challenge solution row."""
        try:
            with self._managed_session() as session:
                solution = session.query(ChallengeSolution).filter_by(tx_hash=tx_hash).first()
                if not solution:
                    bt.logging.warning(f"No challenge solution found with tx_hash: {tx_hash}")
                    return False
                solution.solution_status = solution_status
                bt.logging.info(f"Updated solution status for tx_hash {tx_hash} to {solution_status}")
                return True
        except Exception as e:
            bt.logging.error(f"Error updating challenge solution status: {e}")
            return False

    def update_challenge_solution(
        self,
        tx_hash: str,
        container_id: str,
        container_name: str,
        image_id: str,
        absolute_path_to_solution: str,
        solution_status: str,
    ) -> bool:
        """Update runtime fields on an existing challenge solution row."""
        try:
            with self._managed_session() as session:
                solution = session.query(ChallengeSolution).filter_by(tx_hash=tx_hash).first()
                if not solution:
                    bt.logging.warning(f"No challenge solution found with tx_hash: {tx_hash}")
                    return False
                solution.container_id = container_id
                solution.container_name = container_name
                solution.image_id = image_id
                solution.absolute_path_to_solution = absolute_path_to_solution
                solution.solution_status = solution_status
                bt.logging.info(f" ✅ Updated challenge solution with tx_hash: {tx_hash}")
                return True
        except Exception as e:
            bt.logging.error(f" ❌ Error updating challenge solution: {e}")
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
    ):
        """Insert a new challenge solution record into the database."""
        bt.logging.info(f"Inserting challenge solution with tx_hash: {tx_hash}")
        try:
            with self._managed_session() as session:
                new_solution = ChallengeSolution(
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
                    miner_hotkey=miner_hotkey
                )
                session.add(new_solution)
                bt.logging.info(f" ✅ Inserted challenge solution with challenge_validation_solution_id: {challenge_validation_solution_id}")
                return True
        except Exception as e:
            bt.logging.error(f" ❌ Error inserting challenge solution: {e}")
            return False

    def update_solution_status_in_db(self, solution_location, solution_status):
        """Update the solution status in the database."""
        try:
            with self._managed_session() as session:
                solution = session.query(ChallengeSolution).filter_by(absolute_path_to_solution=solution_location).first()
                if solution:
                    solution.solution_status = solution_status
                    bt.logging.info(f"Updated solution status for {solution_location} to {solution_status}")
                    return True
                else:
                    bt.logging.warning(f"No challenge solution found with absolute_path_to_solution: {solution_location}")
                    return False
        except Exception as e:
            bt.logging.error(f"Error updating solution status: {e}")
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

    def remove_challenge_solution(self, challenge_validation_solution_id):
        """Remove a challenge solution record from the database."""
        try:
            with self._managed_session() as session:
                solution = session.query(ChallengeSolution).filter_by(challenge_validation_solution_id=challenge_validation_solution_id).first()
                if solution:
                    session.delete(solution)
                    bt.logging.info(f"Removed challenge solution with challenge_validation_solution_id: {challenge_validation_solution_id}")
                    return True
                else:
                    bt.logging.warning(f"No challenge solution found with challenge_validation_solution_id: {challenge_validation_solution_id}")
                    return False
        except Exception as e:
            bt.logging.error(f"Error removing challenge solution: {e}")
            return False

    def get_challenge_solution_location(self, container_name):
        """Retrieve a challenge solution record from the database."""
        try:
            with self._managed_session(read_only=True) as session:
                solution = session.query(ChallengeSolution).filter_by(container_name=container_name).first()
                if solution:
                    bt.logging.info(f"Retrieved challenge solution with container_name: {container_name}")
                    return solution
                else:
                    bt.logging.warning(f"No challenge solution found with container_name: {container_name}")
                    return None
        except Exception as e:
            bt.logging.error(f"Error retrieving challenge solution: {e}")
            return None

    def get_container_name_by_solution_location(self, absolute_path_to_solution):
        """Retrieve a challenge solution record from the database."""
        try:
            with self._managed_session(read_only=True) as session:
                solution = session.query(ChallengeSolution).filter_by(absolute_path_to_solution=absolute_path_to_solution).first()
                if solution:
                    bt.logging.info(f"Retrieved challenge solution with absolute_path_to_solution: {absolute_path_to_solution}")
                    return solution.container_name
                else:
                    bt.logging.warning(f"No challenge solution found with absolute_path_to_solution: {absolute_path_to_solution}")
                    return None
        except Exception as e:
            bt.logging.error(f"Error retrieving challenge solution: {e}")
            return None

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

    def get_image_id_from_solution_location(self, absolute_path_to_solution):
        """Retrieve a challenge solution record from the database."""
        try:
            with self._managed_session(read_only=True) as session:
                solution = session.query(ChallengeSolution).filter_by(absolute_path_to_solution=absolute_path_to_solution).first()
                if solution:
                    bt.logging.info(f"Retrieved challenge solution with absolute_path_to_solution: {absolute_path_to_solution}")
                    return solution.image_id
                else:
                    bt.logging.warning(f"No challenge solution found with absolute_path_to_solution: {absolute_path_to_solution}")
                    return None
        except Exception as e:
            bt.logging.error(f"Error retrieving challenge solution: {e}")
            return None

    def get_image_id_by_container_name(self, container_name: str):
        """Return stored image tag/name for a solution row keyed by Docker container name."""
        try:
            with self._managed_session(read_only=True) as session:
                solution = session.query(ChallengeSolution).filter_by(container_name=container_name).first()
                if solution:
                    bt.logging.info(f"Retrieved image_id for container_name: {container_name}")
                    return solution.image_id
                bt.logging.warning(f"No challenge solution found with container_name: {container_name}")
                return None
        except Exception as e:
            bt.logging.error(f"Error retrieving challenge solution by container_name: {e}")
            return None

    def get_image_id_by_container_id(self, container_id: str):
        """Return stored image tag/name for a row whose container_id matches (exact, else prefix for short IDs)."""
        try:
            with self._managed_session(read_only=True) as session:
                solution = session.query(ChallengeSolution).filter_by(container_id=container_id).first()
                if not solution:
                    solution = (
                        session.query(ChallengeSolution)
                        .filter(ChallengeSolution.container_id.like(f"{container_id}%"))
                        .first()
                    )
                if solution:
                    bt.logging.info(f"Retrieved image_id for container_id: {container_id}")
                    return solution.image_id
                bt.logging.warning(f"No challenge solution found with container_id starting with: {container_id}")
                return None
        except Exception as e:
            bt.logging.error(f"Error retrieving challenge solution by container_id: {e}")
            return None

    def remove_solution_from_db_by_conainer_name(self, container_name):
        """Remove a challenge solution record from the database."""
        try:
            with self._managed_session() as session:
                solution = session.query(ChallengeSolution).filter_by(container_name=container_name).first()
                if solution:
                    session.delete(solution)
                    bt.logging.info(f"Removed challenge solution with container_name: {container_name}")
                    return True
                else:
                    bt.logging.warning(f"No challenge solution found with container_name: {container_name}")
                    return False
        except Exception as e:
            bt.logging.error(f"Error removing challenge solution: {e}")
            return False

    def get_submission_id_by_solution_location(self, absolute_path_to_solution):
        """Retrieve a challenge solution record from the database."""
        try:
            with self._managed_session(read_only=True) as session:
                solution = session.query(ChallengeSolution).filter_by(absolute_path_to_solution=absolute_path_to_solution).first()
                if solution:
                    bt.logging.info(f"✅ Retrieved challenge solution with absolute_path_to_solution: {absolute_path_to_solution}")
                    return solution.submission_id
                else:
                    bt.logging.warning(f"No challenge solution found with absolute_path_to_solution: {absolute_path_to_solution}")
                    return None
        except Exception as e:
            bt.logging.error(f"❌ Error retrieving challenge solution: {e}")
            return None

    def get_challenge_milestone_id_by_file_path(self, absolute_path_to_solution):
        """Retrieve a challenge solution record from the database."""
        try:
            with self._managed_session(read_only=True) as session:
                solution = session.query(ChallengeSolution).filter_by(absolute_path_to_solution=absolute_path_to_solution).first()
                if solution:
                    bt.logging.info(f"✅ Retrieved challenge solution with absolute_path_to_solution: {absolute_path_to_solution}")
                    return solution.challenge_milestone_id
                else:
                    bt.logging.warning(f"No challenge solution found with absolute_path_to_solution: {absolute_path_to_solution}")
                    return None
        except Exception as e:
            bt.logging.error(f"❌ Error retrieving challenge solution: {e}")
            return None

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

    def get_solutions_by_milestone(self, challenge_milestone_id: str):
        """Get all ChallengeSolution records for a given milestone."""
        try:
            with self._managed_session(read_only=True) as session:
                solutions = (
                    session.query(ChallengeSolution)
                    .filter_by(challenge_milestone_id=challenge_milestone_id)
                    .order_by(ChallengeSolution.created_at.desc())
                    .all()
                )
                return solutions
        except Exception as e:
            bt.logging.error(f"Error querying solutions by milestone {challenge_milestone_id}: {e}")
            return []

    def get_solution_by_submission_id(self, submission_id: str):
        """Get a ChallengeSolution by submission_id."""
        try:
            with self._managed_session(read_only=True) as session:
                solution = session.query(ChallengeSolution).filter_by(submission_id=submission_id).first()
                return solution
        except Exception as e:
            bt.logging.error(f"Error querying by submission_id {submission_id}: {e}")
            return None

    def get_solution_by_tx_hash(self, tx_hash: str):
        """Get a ChallengeSolution by tx_hash."""
        try:
            with self._managed_session(read_only=True) as session:
                solution = session.query(ChallengeSolution).filter_by(tx_hash=tx_hash).first()
                return solution
        except Exception as e:
            bt.logging.error(f"Error querying by tx_hash {tx_hash}: {e}")
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

        We check both the fast-path maintenance incentive table and the full
        solutions table. This is intentionally two cheap indexed lookups.
        """
        try:
            with self._managed_session(read_only=True) as session:
                # Fast path: maintenance incentives
                if session.query(MinerMaintenanceIncentive).filter_by(tx_hash=tx_hash).first() is not None:
                    return True

                # Fallback: check actual processed solutions
                if session.query(ChallengeSolution).filter_by(tx_hash=tx_hash).first() is not None:
                    return True

                return False
        except Exception:
            # Conservative: if we can't check, assume we haven't seen it
            # (upstream code will handle duplicates safely via on_conflict_do_nothing)
            bt.logging.warning(f"Error checking has_seen_tx_hash for {tx_hash}, assuming not seen")
            return False
