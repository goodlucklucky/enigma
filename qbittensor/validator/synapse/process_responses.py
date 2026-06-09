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
from typing import List, Optional

from qbittensor.utils.request.request_manager import RequestManager
from qbittensor.protocol import SolutionSynapse
from requests import Response
from qbittensor.dto.challenge import (
    ChallengeSubmissionRequest,
    ChallengeSubmissionResponse,
    SolutionCandidate,
    SolutionCandidateProof,
    TransferProof,
)
from qbittensor.validator.solution.run import execute_verified_solution
from qbittensor.database.db_connection import DBConnection
from qbittensor.utils.services.challenges import ChallengesClient

from qbittensor.utils.transfer_proof import verify_transfer_proof_for_synapse
from qbittensor.utils.services.telemetry import TelemetryService


class ResponseProcessor:

    def __init__(
        self,
        request_manager: RequestManager,
        metagraph: bt.Metagraph,
        validator_hotkey: str,
        validator_label: str,
        database_connection: DBConnection,
        subtensor: bt.Subtensor,
        platform_client: ChallengesClient,
        telemetry_service: TelemetryService | None = None,
    ):

        # Setup object references
        self.request_manager: RequestManager = request_manager
        self.metagraph: bt.Metagraph = metagraph
        self.validator_hotkey: str = validator_hotkey
        self.validator_label: str = validator_label
        self.database_connection: DBConnection = database_connection
        self.subtensor: bt.Subtensor = subtensor
        self.platform_client: ChallengesClient = platform_client
        self.telemetry_service: TelemetryService | None = telemetry_service

    def process_synapses(self, synapses: List[SolutionSynapse] | None, validator_busy: bool) -> None:
        """Process inbound synapses from miners"""
        if synapses is None:
            bt.logging.info("No synapses to process")
            return

        bt.logging.info(f"🗂️ Processing {len(synapses)} Synapses")

        solution_started: bool = False

        for uid, synapse in enumerate(synapses):

            # Extract the miner hotkey
            miner_hotkey: str = self.metagraph.hotkeys[uid]

            # Early replay gate.
            # We still honor this even with the verified_tx_hashes cache (which allows
            # cheap short-circuit of re-verification for cross-checks and avoids repeated
            # work on known-bad txs). The cache is tx-only; the actual tx <-> file upload
            # (challenge_validation_solution_id) + challenge/milestone uniqueness is
            # established and enforced by ChallengeSolution rows (see insert_challenge_solution)
            # and by the platform on submit_solution.
            if self.database_connection.db_query.has_seen_tx_hash(synapse.tx_hash):
                # When we have a local binding for this tx, explicitly check the incoming
                # claimed file upload / work identifiers against the previously bound ones.
                # This ensures we still detect and loudly log attempts to reuse a tx for a
                # different file upload, even though the verified cache may contribute to
                # the "seen" decision for some txs.
                binding = self.database_connection.db_query.get_tx_binding_info(synapse.tx_hash)
                attempted_upload = None
                attempted_milestone = None
                if getattr(synapse, "solution_candidate", None):
                    attempted_upload = synapse.solution_candidate.upload_endpoint_id
                    attempted_milestone = synapse.solution_candidate.challenge_milestone_id

                if binding:
                    bound_upload = binding.get("challenge_validation_solution_id")
                    bound_milestone = binding.get("challenge_milestone_id")
                    if (
                        bound_upload
                        and attempted_upload
                        and bound_upload != attempted_upload
                    ):
                        bt.logging.error(
                            "🚨 TX + FILE UPLOAD UNIQUENESS VIOLATION: "
                            f"tx_hash={synapse.tx_hash} is already bound on this validator to "
                            f"upload_endpoint_id={bound_upload} (milestone={bound_milestone}, "
                            f"challenge={binding.get('challenge_id')}). "
                            f"This synapse claims upload_endpoint_id={attempted_upload} "
                            f"(milestone={attempted_milestone}). "
                            "A tx_hash must not be reused for a different file upload / submission / "
                            "challenge / milestone. Skipping (platform and insert_challenge_solution "
                            "provide additional enforcement)."
                        )
                    else:
                        bt.logging.info(
                            f"⏭️  tx_hash {synapse.tx_hash} already processed for matching work item. Skipping."
                        )
                else:
                    bt.logging.info(
                        f"⏭️  tx_hash {synapse.tx_hash} already processed (e.g. prior failed verification via cache). Skipping."
                    )
                continue

            # Guard: some synapses may arrive without a solution candidate
            if synapse.solution_candidate is None:
                continue

            bt.logging.info(
                f"📥 Received SolutionSynapse WITH DATA from miner {miner_hotkey} | "
                f"tx={synapse.tx_hash} | milestone={synapse.solution_candidate.challenge_milestone_id} | "
                f"upload_id={synapse.solution_candidate.upload_endpoint_id}"
            )

            if self.telemetry_service:
                self.telemetry_service.record_event(
                    "solution_received",
                    value=1,
                    miner_hotkey=miner_hotkey,
                    attributes={
                        "tx_hash": synapse.tx_hash,
                        "challenge_milestone_id": synapse.solution_candidate.challenge_milestone_id,
                        "upload_endpoint_id": synapse.solution_candidate.upload_endpoint_id,
                    },
                )

            transfer_proof = TransferProof(
                tx_hash=synapse.tx_hash or "",
                transfer_block_hash=synapse.transfer_block_hash or "",
                transfer_from_ss58=synapse.transfer_from_ss58 or "",
                transfer_to_ss58=synapse.transfer_to_ss58 or "",
                transfer_amount_rao=synapse.transfer_amount_rao or "",
                transfer_proof_message=synapse.transfer_proof_message or "",
                transfer_proof_signature_hex=synapse.transfer_proof_signature_hex or "",
                solution_candidate=SolutionCandidateProof(
                    challenge_milestone_id=synapse.solution_candidate.challenge_milestone_id,
                    upload_endpoint_id=synapse.solution_candidate.upload_endpoint_id,
                    challenge_id=(
                        synapse.challenge_id
                        or synapse.solution_candidate.challenge_id
                    ),
                ),
            )
            challenge_id = (
                synapse.challenge_id
                or synapse.solution_candidate.challenge_id
            )
            if not challenge_id:
                bt.logging.error(
                    f"❌ Synapse from miner '{miner_hotkey}' is missing challenge_id; "
                    f"cannot look up milestone price for tx_hash {synapse.tx_hash}."
                )
                continue
            # Validator must obtain the authoritative fee from the platform via ChallengesClient.
            price_tao = self.platform_client.get_milestone_price_tao(
                challenge_id=challenge_id,
                milestone_id=synapse.solution_candidate.challenge_milestone_id,
            )
            expected_transfer_amount_rao = str(int(bt.Balance.from_tao(price_tao).rao))

            proof_ok, proof_err = verify_transfer_proof_for_synapse(
                transfer_proof,
                miner_hotkey,
                self.subtensor,
                expected_transfer_amount_rao=expected_transfer_amount_rao,
            )

            if proof_ok:
                bt.logging.info("✅ Transfer proof verification passed, continuing with solution processing...")

                if self.telemetry_service:
                    self.telemetry_service.record_event(
                        "transfer_proof_verified",
                        value=1,
                        miner_hotkey=miner_hotkey,
                        attributes={
                            "tx_hash": synapse.tx_hash,
                            "result": "success",
                        },
                    )

                bt.logging.info("📸 Recording maintenance incentive for valid payment")
                ok = self.database_connection.db_query.insert_for_maintenance_incentive(
                    miner_hotkey=miner_hotkey,
                    challenge_milestone_id=synapse.solution_candidate.challenge_milestone_id,
                    tx_hash=synapse.tx_hash,
                )
                if not ok:
                    bt.logging.warning(
                        f"⚠️ Failed to record maintenance incentive for tx_hash {synapse.tx_hash} — "
                        "miner may not receive weight credit for this payment."
                    )

                # Cache the successful verification so cross-checks can avoid re-verifying
                self.database_connection.db_query.record_verified_tx(
                    tx_hash=synapse.tx_hash,
                    success=True,
                    miner_hotkey=miner_hotkey,
                )
            elif synapse.solution_candidate is not None and not proof_ok:
                bt.logging.error(f"❌ Transfer proof verification failed for tx hash {synapse.tx_hash}: {proof_err}")

                if self.telemetry_service:
                    self.telemetry_service.record_event(
                        "transfer_proof_verified",
                        value=0,
                        miner_hotkey=miner_hotkey,
                        attributes={
                            "tx_hash": synapse.tx_hash,
                            "result": "failure",
                            "error": str(proof_err)[:200] if proof_err else None,
                        },
                    )

                # Cache the failed verification (including for future cross-checks)
                self.database_connection.db_query.record_verified_tx(
                    tx_hash=synapse.tx_hash,
                    success=False,
                    error_message=str(proof_err)[:500] if proof_err else None,
                    miner_hotkey=miner_hotkey,
                )
                continue
            else:
                continue

            solution_candidate: SolutionCandidate = synapse.solution_candidate

            bt.logging.info(f"🎯 Found solution candidate ({solution_candidate}) from miner '{miner_hotkey}' with tx hash '{synapse.tx_hash}'")

            if solution_started:
                bt.logging.info("🥾 Skipping solution candidate as we've already kicked off a solution in this iteration.")
                continue

            payload = ChallengeSubmissionRequest(
                address=miner_hotkey,
                upload_endpoint_id=solution_candidate.upload_endpoint_id,
                tx_hash=synapse.tx_hash,
                validator_busy=validator_busy,
                transfer_block_hash=synapse.transfer_block_hash,
                transfer_from_ss58=synapse.transfer_from_ss58,
                transfer_to_ss58=synapse.transfer_to_ss58,
                transfer_amount_rao=synapse.transfer_amount_rao,
                transfer_proof_message=synapse.transfer_proof_message,
                transfer_proof_signature_hex=synapse.transfer_proof_signature_hex,
            )

            try:
                bt.logging.info(
                    f"🚀 Submitting solution to platform (cloud) for tx={synapse.tx_hash} "
                    f"miner={miner_hotkey} milestone={solution_candidate.challenge_milestone_id}"
                )

                if self.telemetry_service:
                    self.telemetry_service.record_event(
                        "platform_submission",
                        value=1,
                        miner_hotkey=miner_hotkey,
                        attributes={
                            "tx_hash": synapse.tx_hash,
                            "milestone_id": solution_candidate.challenge_milestone_id,
                            "upload_endpoint_id": solution_candidate.upload_endpoint_id,
                            "stage": "request",
                        },
                    )

                response = self.platform_client.submit_solution(
                    milestone_id=solution_candidate.challenge_milestone_id,
                    payload=payload,
                )

                if response is None:
                    # 202 (already exists / busy claim) or auth/4xx/5xx handled inside client
                    bt.logging.info(
                        f"ℹ️  Platform returned no response object for tx={synapse.tx_hash} "
                        "(202 duplicate/busy or error — see prior logs). Continuing."
                    )

                    if self.telemetry_service:
                        self.telemetry_service.record_event(
                            "platform_submission",
                            value=0,
                            miner_hotkey=miner_hotkey,
                            attributes={
                                "tx_hash": synapse.tx_hash,
                                "outcome": "202_or_error",
                            },
                        )
                    continue

                if self.telemetry_service:
                    self.telemetry_service.record_event(
                        "platform_submission",
                        value=1,
                        miner_hotkey=miner_hotkey,
                        attributes={
                            "tx_hash": synapse.tx_hash,
                            "submission_id": getattr(response, "id", None),
                            "outcome": "claimed",
                        },
                    )

                if validator_busy:
                    bt.logging.info(
                        f"⏸️  Validator busy — successfully claimed tx_hash {synapse.tx_hash} on platform "
                        "(will not execute this cycle). Platform will re-offer via /next when capacity exists."
                    )
                    continue

                # Extract the download url and run the solution
                download_url: str = response.file_download_url

                image_name, container_id, folder_name = execute_verified_solution(
                    db_conn=self.database_connection,
                    platform_client=self.platform_client,
                    validator_label=self.validator_label,
                    download_url=download_url,
                    challenge_id=challenge_id,
                    challenge_milestone_id=solution_candidate.challenge_milestone_id,
                    challenge_validation_solution_id=solution_candidate.upload_endpoint_id,
                    submission_id=response.id,
                    tx_hash=synapse.tx_hash,
                    miner_hotkey=miner_hotkey,
                    telemetry_service=self.telemetry_service,
                )

                bt.logging.info(f"Started solution with image name {image_name} and container id {container_id}. Solution files are located in {folder_name}")
                solution_started = True

            except Exception as exc:
                bt.logging.error(
                    f"❌ Unexpected error while processing synapse from UID {uid} "
                    f"(miner {miner_hotkey}, tx={synapse.tx_hash}): {type(exc).__name__}: {exc}",
                    exc_info=True,
                )
                # Do not let one bad synapse kill the rest of the round
                continue

    def _log_platform_submission_error(self, response: Response) -> None:
        """Parse and log platform submission errors using the standard error envelope."""
        try:
            body = response.json()
            status = body.get("status_code", response.status_code)
            raw_message = body.get("message", response.text)
            error_code = body.get("error_code")

            # message can be a string or a list of strings
            if isinstance(raw_message, list):
                message = " | ".join(str(m) for m in raw_message)
            else:
                message = str(raw_message)

        except Exception:
            status = response.status_code
            message = response.text
            error_code = None

        # Special case: miner tried to reuse a tx_hash on a different submission
        lower_msg = message.lower()
        if "already been used for a different submission" in lower_msg:
            bt.logging.warning(
                "🚨 Payment reuse detected: tx_hash was already claimed for a different upload_endpoint_id. "
                f"status={status}, message={message}, error_code={error_code}"
            )
        else:
            bt.logging.error(
                f"❌ Failed to submit challenge solution to platform. "
                f"status={status}, message={message}, error_code={error_code}"
            )
