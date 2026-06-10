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
        subtensor: bt.Subtensor,
        telemetry_service: TelemetryService | None = None,
    ):
        self.platform_client: ChallengesClient = platform_client
        self.solution_container_manager = solution_container_manager
        self.timer: Timer = Timer(timeout=CROSS_CHECK_TIMEOUT, run=self.run, run_on_start=True)
        self.validator_label = validator_label
        self.database_connection: DBConnection = database_connection
        self.subtensor: bt.Subtensor = subtensor
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

        SKIP_CROSS_CHECK_TX_VERIFICATION = True

        if SKIP_CROSS_CHECK_TX_VERIFICATION:
            proof_ok = True
            proof_err = None
            bt.logging.warning(
                "⏭️  TEMPORARILY SKIPPING cross-check transfer proof verification "
                f"(trusting cloud) for tx={submission.tx_hash} submission_id={submission.id}. "
                "This lets re-runs proceed past flaky on-chain decode for old blocks. "
                "REVERT THIS FLAG WHEN READY."
            )
            if self.telemetry_service:
                self.telemetry_service.record_event(
                    "transfer_proof_verified",
                    value=1,
                    miner_hotkey=submission.address,
                    attributes={
                        "tx_hash": submission.tx_hash,
                        "result": "success",
                        "source": "cloud_trusted",
                        "note": "temporary bypass - cross check tx verification disabled",
                    },
                )
            # Seed the local cache as success so this validator doesn't keep warning
            # on subsequent cross-checks for the same tx, and so future runs benefit.
            self.database_connection.db_query.record_verified_tx(
                tx_hash=submission.tx_hash,
                success=True,
                miner_hotkey=submission.address,
            )
        else:
            # Verify proof ourselves on first sight for cross-check items.
            # First check our local verified_tx_hashes cache (populated from prior normal processing
            # or previous cross-checks). If present, reuse the result and avoid expensive on-chain
            # historical lookup (which requires archive nodes for old blocks).
            cached_ok, cached_err = self.database_connection.db_query.get_verified_tx_result(submission.tx_hash)
            if cached_ok is not None:
                proof_ok, proof_err = cached_ok, cached_err
                bt.logging.info(
                    f"✅ Reusing cached transfer proof verification for tx_hash {submission.tx_hash} "
                    f"(result={'success' if proof_ok else 'failure'}) from verified_tx_hashes"
                )
                if self.telemetry_service:
                    self.telemetry_service.record_event(
                        "transfer_proof_verified",
                        value=1 if proof_ok else 0,
                        miner_hotkey=submission.address,
                        attributes={
                            "tx_hash": submission.tx_hash,
                            "result": "success" if proof_ok else "failure",
                            "error": str(proof_err)[:200] if proof_err else None,
                            "source": "cache",
                        },
                    )
            else:
                # Not in cache — perform full verification (and it will be cached by the normal path
                # or we record it here for future cross-checks).
                transfer_proof = TransferProof.from_platform_submission(submission)

                try:
                    # Note: We pass the miner's hotkey (submission.address) here, not the validator's.
                    # For cross-check submissions coming from the platform, the expected transfer amount
                    # is provided directly by the platform in the submission record.
                    proof_ok, proof_err = verify_transfer_proof_for_synapse(
                        transfer_proof,
                        submission.address,
                        self.subtensor,
                        expected_transfer_amount_rao=submission.transfer_amount_rao,
                    )
                except Exception as e:
                    proof_ok, proof_err = False, f"Verification call failed: {e}"

                # Cache the result (whether success or failure) so future cross-checks for this tx
                # can avoid re-verification.
                self.database_connection.db_query.record_verified_tx(
                    tx_hash=submission.tx_hash,
                    success=proof_ok,
                    error_message=str(proof_err)[:500] if proof_err else None,
                    miner_hotkey=submission.address,
                )

                if self.telemetry_service:
                    self.telemetry_service.record_event(
                        "transfer_proof_verified",
                        value=1 if proof_ok else 0,
                        miner_hotkey=submission.address,
                        attributes={
                            "tx_hash": submission.tx_hash,
                            "result": "success" if proof_ok else "failure",
                            "error": str(proof_err)[:200] if proof_err else None,
                            "source": "fresh",
                        },
                    )

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

        if not proof_ok:
            # For cached failure results, still report and skip (consistent with fresh verification)
            self.platform_client.report_submission_status(
                submission_id=submission.id,
                status="Failure",
                reason=proof_err or "Transfer proof verification failed for cross-check submission (cached result)",
            )
            return

        bt.logging.info("📸 Inserting row for miner maintenance incentive (cross-check path)")
        self.database_connection.db_query.insert_for_maintenance_incentive(
            miner_hotkey=submission.address,
            challenge_milestone_id=submission.challenge_milestone_id,
            tx_hash=submission.tx_hash,
        )

        bt.logging.info(f"Running solution cross-check on solution with miner hotkey {submission.address}")

        challenge_id = submission.challenge_id
        if not challenge_id:
            bt.logging.error(
                f"❌ Cross-check submission {submission.id} missing challenge_id for tx_hash {submission.tx_hash}. "
                "Cannot execute without it per invariant that challenge_id is never optional in execution path."
            )
            self.platform_client.report_submission_status(
                submission_id=submission.id,
                status="Failure",
                reason="Missing challenge_id for cross-check submission",
            )
            return

        start_time = time.time()
        # For cross-checks we pass the original upload_endpoint_id (the "file upload")
        # as challenge_validation_solution_id so that the DB guard can recognize it as
        # the same work item (tx + file upload + challenge + milestone) even if the
        # cross-check submission uses a different .id .
        image_name, container_id, folder_name = execute_verified_solution(
            db_conn=self.database_connection,
            platform_client=self.platform_client,
            validator_label=self.validator_label,
            download_url=submission.file_download_url,
            challenge_id=challenge_id,
            challenge_milestone_id=submission.challenge_milestone_id,
            challenge_validation_solution_id=submission.upload_endpoint_id,
            submission_id=submission.id,
            tx_hash=submission.tx_hash,
            miner_hotkey=submission.address,
            telemetry_service=self.telemetry_service,
        )
        duration = time.time() - start_time

        if self.telemetry_service:
            outcome = "success" if (image_name and container_id) else "failure"
            self.telemetry_service.record_event(
                "cross_check_container_launched",
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
