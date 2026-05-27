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

from dataclasses import dataclass
from typing import Any

import bittensor as bt
import click
from bittensor_wallet import Keypair
from rich.console import Console
from rich.text import Text

from qbittensor.utils.transfer_proof import TRANSFER_DEST_SS58


@dataclass(frozen=True)
class TransferProofTx:
    """On-chain transfer identifiers after inclusion (required to verify the extrinsic)."""

    extrinsic_hash: str
    block_hash: str


def transfer_proof_tx_from_receipt(response: Any) -> TransferProofTx:
    """Extract extrinsic hash and inclusion block hash from a submit_extrinsic receipt."""
    ex_hash = getattr(response, "extrinsic_hash", None)
    block_hash = getattr(response, "block_hash", None)
    if isinstance(response, dict):
        ex_hash = ex_hash or response.get("extrinsic_hash")
        block_hash = block_hash or response.get("block_hash")
    if not ex_hash or not block_hash:
        raise ValueError(
            "Transfer submitted but extrinsic_hash and block_hash were not both returned."
        )
    return TransferProofTx(extrinsic_hash=str(ex_hash), block_hash=str(block_hash))


def transfer_fee_extrinsic_subtensor(
    *,
    subtensor: bt.Subtensor,
    source_ss58: str,
    source_mnemonic: str,
    fee_tao: float,
) -> TransferProofTx:
    """
    Submit Balances.transfer_keep_alive for the explicitly supplied fee_tao (RAO value)
    to TRANSFER_DEST_SS58. fee_tao MUST come from a successful call to
    ChallengesClient.get_milestone_price_tao(challenge_id=..., milestone_id=...)
    (or the convenience get_milestone_transfer_amount_rao()). No internal fallback is permitted.
    Raises ValueError on any failure.
    """
    if fee_tao is None:
        raise ValueError("fee_tao is required and must be obtained from the Challenges API")
    phrase = (source_mnemonic or "").strip()
    if not phrase:
        raise ValueError("A mnemonic/seed phrase is required to sign the transfer.")
    try:
        keypair = Keypair.create_from_mnemonic(phrase)
    except Exception as e:
        raise ValueError(f"Invalid mnemonic/seed phrase: {e}") from e
    if keypair.ss58_address != source_ss58:
        raise ValueError("Source SS58 does not match the provided mnemonic's address.")
    transfer_call = subtensor.substrate.compose_call(
        call_module="Balances",
        call_function="transfer_keep_alive",
        call_params={
            "dest": TRANSFER_DEST_SS58,
            "value": bt.Balance.from_tao(fee_tao).rao,
        },
    )
    extrinsic = subtensor.substrate.create_signed_extrinsic(
        call=transfer_call,
        keypair=keypair,
    )
    response = subtensor.substrate.submit_extrinsic(
        extrinsic,
        wait_for_inclusion=True,
        wait_for_finalization=False,
    )
    if not response.is_success:
        raise ValueError(f"Transfer extrinsic failed: {response.error_message}")
    return transfer_proof_tx_from_receipt(response)


def transfer_tao_for_submission(
    *,
    console: Console,
    source_ss58: str,
    source_mnemonic: str,
    network: str,
    fee_tao: float,
    milestone_id: str | None = None,
    challenge_id: str | None = None,
) -> TransferProofTx:
    """Submit TAO transfer and return extrinsic + inclusion block hash for verification.

    fee_tao MUST be provided by the caller (obtained via
    ChallengesClient.get_milestone_price_tao(challenge_id=..., milestone_id=...)
    or get_milestone_transfer_amount_rao()). This function does not perform any Challenges API calls itself.
    """
    if not milestone_id:
        raise click.ClickException("milestone_id is required")

    if fee_tao is None or fee_tao <= 0:
        raise click.ClickException("fee_tao is required and must be greater than 0")

    console.print(
        Text(
            f"Using API priceTao={fee_tao} for milestone {milestone_id}",
            style="dim cyan",
        )
    )

    try:
        with bt.Subtensor(network=network) as subtensor:
            proof_tx = transfer_fee_extrinsic_subtensor(
                subtensor=subtensor,
                source_ss58=source_ss58,
                source_mnemonic=source_mnemonic,
                fee_tao=fee_tao,
            )
    except ValueError as e:
        raise click.ClickException(f"Transfer transaction failed: {e}") from e
    except Exception as e:
        raise click.ClickException(f"Transfer transaction failed: {e}") from e

    console.print(
        Text(
            f"Proof tx: {proof_tx.extrinsic_hash} (block {proof_tx.block_hash})",
            style="bold green",
        )
    )
    return proof_tx
