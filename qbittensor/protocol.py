# The MIT License (MIT)
# Copyright © 2026 qBitTensor Labs

# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the “Software”), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.

# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

"""
Protocol for Challenges
"""
from __future__ import annotations
import bittensor as bt
from pydantic import Field, BaseModel
from typing import Optional
from qbittensor.dto.challenge import SolutionCandidate


class MinerSubmissionStatus(BaseModel):
    status: str = Field(
        description="The status of the miner submission.",
    )
    miner_hotkey: Optional[str] = Field(
        default=None,
        description="The hotkey of the miner that submitted the miner submission."
    )
    tx_hash: Optional[str] = Field(
        default=None,
        description="The transaction hash of the miner submission."
    )
    challenge_milestone_id: Optional[str] = Field(
        default=None,
        description="Challenge milestone this submission belongs to.",
    )


class SolutionSynapse(bt.Synapse):
    validator_busy: bool = Field(
        description="Whether or not the validator sendig this request is currently running a solution",
    )

    solution_candidate: Optional[SolutionCandidate] = Field(
        default=None,
        description="The candidate solution for this synapse."
    )

    submission_statuses: Optional[list[MinerSubmissionStatus]] = Field(
        default=None,
        description="The statuses of the miner submissions.",
    )

    challenge_id: Optional[str] = Field(
        default=None,
        description="Top-level challenge id (used for milestone price lookup).",
    )

    tx_hash: Optional[str] = Field(
        default=None,
        description="The transaction hash of the solution."
    )

    transfer_block_hash: Optional[str] = Field(
        default=None,
        description="Block hash where the transfer extrinsic was included.",
    )

    transfer_from_ss58: Optional[str] = Field(
        default=None,
        description="Coldkey SS58 that sent the TAO transfer.",
    )
    transfer_to_ss58: Optional[str] = Field(
        default=None,
        description="Destination SS58 of the TAO transfer.",
    )
    transfer_amount_rao: Optional[str] = Field(
        default=None,
        description="Transfer amount in RAO as a decimal string.",
    )
    transfer_proof_message: Optional[str] = Field(
        default=None,
        description="Exact UTF-8 message signed by the miner hotkey.",
    )
    transfer_proof_signature_hex: Optional[str] = Field(
        default=None,
        description="Hex-encoded Sr25519 signature over transfer_proof_message.",
    )

    def __repr__(self) -> str:
        return (
            "SolutionSynapse(\n"
            f"  validator_busy={self.validator_busy!r},\n"
            f"  solution_candidate={self.solution_candidate!r},\n"
            f"  challenge_id={self.challenge_id!r},\n"
            f"  tx_hash={self.tx_hash!r},\n"
            f"  transfer_block_hash={self.transfer_block_hash!r},\n"
            f"  transfer_from_ss58={self.transfer_from_ss58!r},\n"
            f"  transfer_to_ss58={self.transfer_to_ss58!r},\n"
            f"  transfer_amount_rao={self.transfer_amount_rao!r},\n"
            f"  transfer_proof_message={self.transfer_proof_message!r},\n"
            f"  transfer_proof_signature_hex={self.transfer_proof_signature_hex!r},\n"
            ")"
        )

    def __str__(self) -> str:
        return self.__repr__()
