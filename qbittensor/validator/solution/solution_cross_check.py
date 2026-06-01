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

from qbittensor.database.db_connection import DBConnection
from qbittensor.dto.challenge import ChallengeSubmissionRead, TransferProof
from qbittensor.utils.timer import Timer
from .solution_container_manager import SolutionContainerManager
from .run import execute_verified_solution
from qbittensor.constants import CROSS_CHECK_TIMEOUT
from qbittensor.utils.transfer_proof import verify_transfer_proof_for_synapse
from qbittensor.utils.services.challenges import ChallengesClient
from qbittensor.utils.services.telemetry import TelemetryService
import bittensor as bt
import time


class SolutionCrossChecker:

    def __init__(
        self,
        validator_label: str,
        platform_client: ChallengesClient,
        solution_container_manager: SolutionContainerManager,
        database_connection: DBConnection,
        subtensor=None,  # Optional: needed for full tx proof verification on first-time cross-check items
        telemetry_service: TelemetryService | None = None,
    ):
        self.platform_client: ChallengesClient = platform_client
        self.solution_container_manager = solution_container_manager
        self.timer: Timer = Timer(timeout=CROSS_CHECK_TIMEOUT, run=self.run, run_on_start=True)
        self.validator_label = validator_label
        self.database_connection: DBConnection = database_connection
        self.subtensor = subtensor
        self.telemetry_service: TelemetryService | None = telemetry_service

    def run(self) -> None:
        """Poll for cross-check work when idle."""
        bt.logging.info("🎁 Running cross-check for solutions")

        if self.solution_container_manager.validator_is_busy():
            bt.logging.info("🐝 Validator is busy. Not performing any cross-checks.")
            return

        submission: ChallengeSubmissionRead | None = self.platform_client.get_next_cross_check_submission()
        if submission is None:
            bt.logging.info("🚫 Found no solutions to cross-check")
            return

        if self.telemetry_service:
            self.telemetry_service.record_event(
                "cross_check_work_received",
                value=1,
                miner_hotkey=submission.address,
                attributes={
                    "submission_id": submission.id,
                    "tx_hash": submission.tx_hash,
                    "challenge_milestone_id": submission.challenge_milestone_id,
                },
            )

        # Verify proof ourselves on first sight for cross-check items.
        if not self.database_connection.db_query.has_seen_tx_hash(submission.tx_hash):
            transfer_proof = TransferProof.from_platform_submission(submission)

            try:
                subtensor = getattr(self, "subtensor", None)
                # Note: We pass the miner's hotkey (submission.address) here, not the validator's.
                # For cross-check submissions coming from the platform, the expected transfer amount
                # is provided directly by the platform in the submission record.
                proof_ok, proof_err = verify_transfer_proof_for_synapse(
                    transfer_proof,
                    submission.address,
                    subtensor,
                    expected_transfer_amount_rao=submission.transfer_amount_rao,
                )
            except Exception as e:
                proof_ok, proof_err = False, f"Verification call failed: {e}"

            if not proof_ok:
                bt.logging.error(
                    f"🚨 Cross-check verification failed for previously unseen tx_hash. "
                    f"tx_hash={submission.tx_hash}, milestone={submission.challenge_milestone_id}, "
                    f"cross_check_id={submission.id}. Error: {proof_err}"
                )
                # Report failure to platform and skip entirely (no incentive, no run)
                self.platform_client.report_submission_status(
                    submission_id=submission.id,
                    status="Failure",
                    reason=proof_err or "Transfer proof verification failed for cross-check submission",
                )
                return
            else:
                bt.logging.info(
                    f"✅ Successfully verified previously unseen cross-check tx_hash "
                    f"{submission.tx_hash} for milestone {submission.challenge_milestone_id}"
                )

        bt.logging.info("📸 Inserting row for miner maintenance incentive (cross-check path)")
        self.database_connection.db_query.insert_for_maintenance_incentive(
            miner_hotkey=submission.address,
            challenge_milestone_id=submission.challenge_milestone_id,
            tx_hash=submission.tx_hash,
        )

        bt.logging.info(f"Running solution cross-check on solution with miner hotkey {submission.address}")

        start_time = time.time()
        image_name, container_id, folder_name = execute_verified_solution(
            db_conn=self.database_connection,
            platform_client=self.platform_client,
            validator_label=self.validator_label,
            download_url=submission.file_download_url,
            challenge_id=submission.challenge_id,
            challenge_milestone_id=submission.challenge_milestone_id,
            challenge_validation_solution_id=submission.id,
            submission_id=submission.id,
            tx_hash=submission.tx_hash,
            miner_hotkey=submission.address,
            telemetry_service=self.telemetry_service,
        )
        duration = time.time() - start_time

        if self.telemetry_service:
            outcome = "success" if (image_name and container_id) else "failure"
            self.telemetry_service.record_event(
                "cross_check_execution_completed",
                value=duration,
                miner_hotkey=submission.address,
                attributes={
                    "submission_id": submission.id,
                    "tx_hash": submission.tx_hash,
                    "outcome": outcome,
                },
            )

        if image_name is None or container_id is None or folder_name is None:
            # execute_verified_solution already reported failure to platform when possible
            return

        bt.logging.info(
            f"✨ Solution cross-check running on solution {submission.id} "
            f"with image name {image_name}, container id {container_id}"
        )
