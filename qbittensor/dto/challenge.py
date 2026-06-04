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

from __future__ import annotations
from typing import Optional
from pydantic import BaseModel

from qbittensor.database.miner.db_models import MinerSubmission


class SolutionCandidate(BaseModel):
    """
    Data needed by the validator to process/run a solution.
    """
    challenge_milestone_id: str
    upload_endpoint_id: str
    challenge_id: Optional[str] = None

    @staticmethod
    def from_miner_submission(miner_submission: MinerSubmission) -> SolutionCandidate:
        return SolutionCandidate(
            challenge_milestone_id=miner_submission.challenge_milestone_id,
            upload_endpoint_id=miner_submission.upload_id,
            challenge_id=miner_submission.challenge_id,
        )

    def __repr__(self) -> str:
        return "SolutionCandidate(challenge_milestone_id: {}, upload_endpoint_id: {}, challenge_id: {})".format(
            self.challenge_milestone_id,
            self.upload_endpoint_id,
            self.challenge_id,
        )

    def __str__(self) -> str:
        return self.__repr__()


# Request to challenges/milestones/:milestone_id/submissions
class ChallengeSubmissionRequest(BaseModel):
    address: str
    upload_endpoint_id: str
    tx_hash: str
    validator_busy: bool = False
    transfer_block_hash: str
    transfer_from_ss58: str
    transfer_to_ss58: str
    transfer_amount_rao: str
    transfer_proof_message: str
    transfer_proof_signature_hex: str


class ChallengeSubmissionResponse(BaseModel):
    id: str
    challenge_milestone_id: str
    file_download_url: str
    tx_hash: str


class ChallengeSubmissionVerifyUploadAddressResponse(BaseModel):
    id: str
    url: str


class ChallengeSubmissionRead(BaseModel):
    id: str
    challenge_id: Optional[str] = None
    challenge_milestone_id: str
    file_download_url: str
    upload_endpoint_id: str
    tx_hash: str
    address: str
    transfer_block_hash: str
    transfer_from_ss58: str
    transfer_to_ss58: str
    transfer_amount_rao: str
    transfer_proof_message: str
    transfer_proof_signature_hex: str

    def __repr__(self) -> str:
        return (
            f"ChallengeSubmissionRead(id={self.id}, "
            f"challenge_id={self.challenge_id}, "
            f"challenge_milestone_id={self.challenge_milestone_id}, "
            f"tx_hash={self.tx_hash}, address={self.address})"
        )

    def __str__(self) -> str:
        return self.__repr__()


class SolutionCandidateProof(BaseModel):
    """
    Minimal subset of SolutionCandidate needed for transfer proof verification.
    Used by both real synapses and platform-provided cross-check submissions.
    """
    challenge_milestone_id: str
    upload_endpoint_id: str
    challenge_id: Optional[str] = None


class TransferProof(BaseModel):
    """
    A self-contained, verifiable representation of a miner's transfer proof
    for a solution submission.

    This is the canonical type that should be passed to
    `verify_transfer_proof_for_synapse()`.
    """
    tx_hash: str
    transfer_block_hash: str
    transfer_from_ss58: str
    transfer_to_ss58: str
    transfer_amount_rao: str
    transfer_proof_message: str
    transfer_proof_signature_hex: str

    solution_candidate: SolutionCandidateProof

    # --- Construction helpers -------------------------------------------------

    @classmethod
    def from_platform_submission(cls, submission: "ChallengeSubmissionRead") -> "TransferProof":
        """
        Build a TransferProof from a ChallengeSubmissionRead returned by the platform
        (used during cross-check / /submissions/next flow).
        """
        return cls(
            tx_hash=submission.tx_hash,
            transfer_block_hash=submission.transfer_block_hash,
            transfer_from_ss58=submission.transfer_from_ss58,
            transfer_to_ss58=submission.transfer_to_ss58,
            transfer_amount_rao=submission.transfer_amount_rao,
            transfer_proof_message=submission.transfer_proof_message,
            transfer_proof_signature_hex=submission.transfer_proof_signature_hex,
            solution_candidate=SolutionCandidateProof(
                challenge_milestone_id=submission.challenge_milestone_id,
                upload_endpoint_id=submission.upload_endpoint_id,
                challenge_id=submission.challenge_id,
            ),
        )
