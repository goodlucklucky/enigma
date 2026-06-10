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
from qbittensor.validator.solution.solution_container_manager import SolutionContainerManager

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
        solution_container_manager: SolutionContainerManager | None = None,
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
        self.solution_container_manager: SolutionContainerManager | None = solution_container_manager

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

            # Early replay / binding gate.
            # We check for local work-item bindings (ChallengeSolution + maintenance incentive).
            # The verified_tx_hashes cache is *not* used here for skipping offers — it is
            # a tx-only cache used only to short-circuit transfer proof re-verification
            # (see get_verified_tx_result below and the cross-check path).
            #
            # Real tx <-> specific file upload (challenge_validation_solution_id) + challenge/milestone
            # uniqueness is established by ChallengeSolution rows (see insert_challenge_solution / get_tx_binding_info)
            # and by the platform on submit_solution.
            #
            # tx cache + maintenance incentive are recorded once on successful proof verification
            # (even for 202/busy claims). ChallengeSolution is only created on actual local execution
            # (platform 201 response or cross-check via /next).
            if self.database_connection.db_query.has_seen_tx_hash(synapse.tx_hash):
                # We have a local binding for this tx. Check the incoming claimed file upload /
                # work identifiers against the previously bound ones. This catches attempts to
                # reuse a tx for a different upload even after the verified cache was added.
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
                    continue
                # No binding row (rare if has_seen only looks at bindings). Fall through to normal processing.
                # We will still use the verified cache for verification short-circuit below.

            # Separate cheap check: if we have a *cached failure* for this tx (from prior direct
            # processing or cross-check), skip re-processing the offer. Success-only cache entries
            # (e.g. from a prior busy/202 claim with no local ChallengeSolution) do not cause a skip here.
            cached_ok, cached_err = self.database_connection.db_query.get_verified_tx_result(synapse.tx_hash)
            if cached_ok is False:
                bt.logging.info(
                    f"⏭️  tx_hash {synapse.tx_hash} has a prior cached verification failure. Skipping."
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

            # Short-circuit transfer proof verification using the verified_tx_hashes cache
            # when we have a prior result (populated from previous direct processing or cross-checks).
            # This is the narrow use of the tx cache — it avoids expensive on-chain historical lookups
            # without causing a full early skip of the offer (see the binding gate above).
            cached_ok, cached_err = self.database_connection.db_query.get_verified_tx_result(synapse.tx_hash)
            if cached_ok is not None:
                proof_ok, proof_err = cached_ok, cached_err
                bt.logging.debug(
                    f"Reusing cached transfer proof verification for tx={synapse.tx_hash} "
                    f"(result={'success' if proof_ok else 'failure'})"
                )
            else:
                proof_ok, proof_err = verify_transfer_proof_for_synapse(
                    transfer_proof,
                    miner_hotkey,
                    self.subtensor,
                    expected_transfer_amount_rao=expected_transfer_amount_rao,
                )
                # Record (success or failure) so future direct offers and cross-checks can short-circuit.
                # This + maintenance incentive are the "record once" items on proof success.
                self.database_connection.db_query.record_verified_tx(
                    tx_hash=synapse.tx_hash,
                    success=proof_ok,
                    error_message=str(proof_err)[:500] if proof_err else None,
                    miner_hotkey=miner_hotkey,
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

                # Note: verified_tx was already recorded above (either from cache hit or fresh verification).
                # ChallengeSolution is *not* created here — only on 201 response or cross-check /next.
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

                # verified_tx was already recorded above for this failure case.
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
                    f"miner={miner_hotkey} milestone={solution_candidate.challenge_milestone_id} "
                    f"(validator_busy={validator_busy})"
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
                    # 202 means either:
                    # - duplicate / already claimed, or
                    # - we sent validator_busy=True and the platform correctly recorded the
                    #   submission + NOT_RUN rows for this validator (for later re-offer via /next).
                    if validator_busy:
                        bt.logging.info(
                            f"⏸️  Validator busy — successfully recorded platform claim for tx={synapse.tx_hash} "
                            "(submission created with NOT_RUN for this validator). "
                            "Will not execute this cycle; platform will re-offer via /next when capacity exists."
                        )
                    else:
                        bt.logging.info(
                            f"ℹ️  Platform returned no response object for tx={synapse.tx_hash} "
                            "(202 duplicate or error — see prior logs). Continuing."
                        )

                    if self.telemetry_service:
                        outcome = "busy_claim_recorded" if validator_busy else "202_or_error"
                        self.telemetry_service.record_event(
                            "platform_submission",
                            value=0 if not validator_busy else 1,
                            miner_hotkey=miner_hotkey,
                            attributes={
                                "tx_hash": synapse.tx_hash,
                                "outcome": outcome,
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

                # This path is only reached if the platform returned a full response object (with
                # submission id + download url) even though our local snapshot said we were busy.
                # Platform create() for brand new subs now respects isBusy and returns null (202).
                # tryClaim paths (late reports on existing subs) previously could still promote to
                # Running for a busy caller on all-NotRun primaries or cross-checks. We now prevent
                # that in tryClaim, but keep this block + explicit NotRun report for robustness and
                # to clean any pre-fix or racy promotions. Preserves the "do not execute when busy" rule.
                if validator_busy:
                    bt.logging.info(
                        f"⏸️  Validator busy — successfully claimed tx_hash {synapse.tx_hash} on platform "
                        "(will not execute this cycle). Reporting NotRun to free the slot; will be re-offered via /next when capacity exists."
                    )
                    # Platform may have set our row to Running (e.g. late-claim/tryClaim on eligible sub even
                    # when we reported busy). Immediately report NotRun so it does not become a phantom
                    # "Running" with zero containers. This keeps cloud capacity view accurate (we enforce
                    # only 1 concurrent via MAX_SOLUTIONS=1 + docker ps) and returns the work to NotRun
                    # backlog for a future idle validator (including us) to pick via cross-check /next.
                    if response is not None:
                        submission_id = getattr(response, "id", None)
                        if submission_id:
                            self.platform_client.report_submission_status(
                                submission_id=submission_id,
                                status="NotRun",
                                reason="Validator was at capacity (busy=true) when this claim was processed; execution skipped. Reset to NotRun so platform can re-offer via /submissions/next to an idle validator.",
                            )
                    continue

                # Extract the download url and run the solution
                download_url: str = response.file_download_url

                # Use the launching() context manager when available. This makes the
                # commitment to a slot visible immediately (for busy snapshots and
                # future /next checks) and ensures we always release it.
                if self.solution_container_manager is not None:
                    launch_ctx = self.solution_container_manager.launching()
                else:
                    # Fallback for tests / old construction that didn't pass the manager
                    from contextlib import nullcontext
                    launch_ctx = nullcontext()

                with launch_ctx:
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
