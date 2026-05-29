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

"""
Tests for the on-chain TAO fee transfer helper used by the miner CLI.

Mocking strategy:
- We deliberately mock at the bittensor/substrate boundary (`bt.Subtensor` and
  its `.substrate` attribute). This is the correct seam for this module because
  it performs a specific `Balances.transfer_keep_alive` extrinsic and never
  talks to the platform HTTP APIs.
- Contrast with post-consolidation platform code (ChallengesClient /
  RequestManager), which should be mocked at the client level in other tests.
- Receipt mocks are intentionally minimal and shape-based (only the fields the
  code actually reads) so the tests remain stable across substrateinterface
  versions.
- All success paths now use a silent Console to avoid polluting test output.
"""

from unittest.mock import MagicMock, Mock, patch

import bittensor as bt
import click
import pytest

from qbittensor.cli.miner.tao_transfer import (
    TransferProofTx,
    transfer_fee_extrinsic_subtensor,
    transfer_proof_tx_from_receipt,
    transfer_tao_for_submission,
)
from qbittensor.utils.transfer_proof import TRANSFER_DEST_SS58

# -----------------------------------------------------------------------------
# Reusable mock factories for the substrate layer.
# These are intentionally shape-based (only the attributes/methods touched
# by the code under test) so they remain resilient to substrateinterface
# version differences. This is the appropriate mock boundary for on-chain
# transfer logic (distinct from the ChallengesClient/RequestManager boundary
# used elsewhere after the platform API consolidation).
# -----------------------------------------------------------------------------


def make_receipt(*, success: bool = True, extrinsic_hash: str = "0xabc", block_hash: str = "0xdef") -> Mock:
    """Create a minimal mock that looks like a successful (or failing) submit_extrinsic receipt."""
    receipt = Mock()
    receipt.is_success = success
    receipt.error_message = None if success else "some extrinsic error"
    receipt.extrinsic_hash = extrinsic_hash
    receipt.block_hash = block_hash
    return receipt


def make_subtensor_with_receipt(receipt: Mock) -> MagicMock:
    """Return a bt.Subtensor-like mock whose .substrate chain returns the given receipt."""
    substrate = Mock()
    substrate.compose_call.return_value = Mock(name="transfer_call")
    substrate.create_signed_extrinsic.return_value = Mock(name="signed_extrinsic")
    substrate.submit_extrinsic.return_value = receipt

    # MagicMock is required so we can easily attach __enter__/__exit__ for
    # the context-manager protocol used by the high-level wrapper.
    subtensor = MagicMock()
    subtensor.substrate = substrate
    subtensor.__enter__.return_value = subtensor
    subtensor.__exit__.return_value = False
    return subtensor


def silent_console():
    """Return a rich Console that writes nowhere (prevents test output pollution)."""
    from rich.console import Console
    import io

    return Console(file=io.StringIO(), force_terminal=False)


class TestTransferProofTxFromReceipt:
    def test_from_object_attributes(self):
        receipt = Mock(extrinsic_hash="0xex", block_hash="0xblock")
        proof = transfer_proof_tx_from_receipt(receipt)
        assert proof == TransferProofTx("0xex", "0xblock")

    def test_from_dict(self):
        proof = transfer_proof_tx_from_receipt(
            {"extrinsic_hash": "0xex", "block_hash": "0xblock"}
        )
        assert proof.extrinsic_hash == "0xex"

    def test_missing_fields_raises(self):
        with pytest.raises(ValueError, match="extrinsic_hash and block_hash"):
            transfer_proof_tx_from_receipt({})


class TestTransferFeeExtrinsicSubtensor:
    """Tests for the Keypair + batch + remark."""

    def test_ss58_mismatch_raises(self):
        keypair = Mock()
        keypair.ss58_address = "5DifferentAddress"

        with pytest.raises(ValueError, match="does not match"):
            transfer_fee_extrinsic_subtensor(
                subtensor=Mock(),
                source_ss58="5Expected",
                keypair=keypair,
                fee_tao=0.5,
                miner_hotkey="5Hotkey",
                milestone_id="m-1",
                upload_endpoint_id="upload-xyz",
            )

    def test_fee_tao_none_raises(self):
        keypair = Mock()
        keypair.ss58_address = "5Addr"

        with pytest.raises(ValueError, match="Challenges API"):
            transfer_fee_extrinsic_subtensor(
                subtensor=Mock(),
                source_ss58="5Addr",
                keypair=keypair,
                fee_tao=None,
                miner_hotkey="5Hotkey",
                milestone_id="m-1",
                upload_endpoint_id="upload-xyz",
            )

    def test_happy_path_builds_batch_and_submits_extrinsic(self):
        """New happy path: Keypair + batch_all (transfer + remark) is built and submitted."""
        receipt = make_receipt(success=True, extrinsic_hash="0xfeedface", block_hash="0xdeadbeef")
        fake_subtensor = make_subtensor_with_receipt(receipt)

        keypair = Mock()
        keypair.ss58_address = "5MatchingAddress"

        proof = transfer_fee_extrinsic_subtensor(
            subtensor=fake_subtensor,
            source_ss58="5MatchingAddress",
            keypair=keypair,
            fee_tao=0.123,
            miner_hotkey="5Hotkey123",
            milestone_id="milestone-abc",
            upload_endpoint_id="upload-12345",
        )

        assert isinstance(proof, TransferProofTx)
        assert proof.extrinsic_hash == "0xfeedface"
        assert proof.block_hash == "0xdeadbeef"

        substrate = fake_subtensor.substrate

        # We now expect two compose_call calls (transfer + remark) inside batch_all
        assert substrate.compose_call.call_count >= 2

        # The final call should be the Utility.batch_all
        last_call = substrate.compose_call.call_args_list[-1]
        assert last_call.kwargs["call_module"] == "Utility"
        assert last_call.kwargs["call_function"] == "batch_all"

        substrate.create_signed_extrinsic.assert_called_once()
        substrate.submit_extrinsic.assert_called_once_with(
            substrate.create_signed_extrinsic.return_value,
            wait_for_inclusion=True,
            wait_for_finalization=False,
        )


@pytest.fixture
def patch_keypair_mismatch():
    """Pytest fixture that makes Keypair.create_from_mnemonic return a keypair
    whose ss58_address deliberately does not match the caller's expectation.
    Use as a context manager inside tests that need the mismatch behavior.
    """
    from unittest.mock import patch

    keypair = Mock()
    keypair.ss58_address = "5DifferentAddressThatWillNotMatch"
    return patch(
        "qbittensor.cli.miner.tao_transfer.Keypair.create_from_mnemonic",
        return_value=keypair,
    )


class TestTransferTaoForSubmission:
    """Tests for transfer_tao_for_submission (Keypair-based)."""

    def test_requires_milestone_id(self):
        keypair = Mock()
        keypair.ss58_address = "5Addr"

        with pytest.raises(click.ClickException, match="milestone_id is required"):
            transfer_tao_for_submission(
                console=silent_console(),
                source_ss58="5Addr",
                keypair=keypair,
                network="finney",
                fee_tao=0.5,
                miner_hotkey="5H",
                milestone_id="",   # empty to trigger the check
                upload_endpoint_id="u-1",
            )

    def test_fee_tao_none_or_non_positive_raises(self):
        keypair = Mock()
        keypair.ss58_address = "5Addr"

        for bad_fee in (None, 0, -0.1):
            with pytest.raises(click.ClickException, match="fee_tao is required and must be greater than 0"):
                transfer_tao_for_submission(
                    console=silent_console(),
                    source_ss58="5Addr",
                    keypair=keypair,
                    network="finney",
                    fee_tao=bad_fee,
                    miner_hotkey="5H",
                    milestone_id="m-123",
                    upload_endpoint_id="u-1",
                )

    def test_happy_path_uses_context_manager_and_returns_proof(self):
        receipt = make_receipt(success=True, extrinsic_hash="0xproof", block_hash="0xblock")
        fake_subtensor = make_subtensor_with_receipt(receipt)

        keypair = Mock()
        keypair.ss58_address = "5HappyPathAddr"

        with patch(
            "qbittensor.cli.miner.tao_transfer.bt.Subtensor",
        ) as mock_subtensor_cls:
            mock_subtensor_cls.return_value.__enter__.return_value = fake_subtensor

            proof = transfer_tao_for_submission(
                console=silent_console(),
                source_ss58="5HappyPathAddr",
                keypair=keypair,
                network="finney",
                fee_tao=0.42,
                miner_hotkey="5Hotkey",
                milestone_id="m-123",
                upload_endpoint_id="upload-1",
            )

        assert isinstance(proof, TransferProofTx)
        assert proof.extrinsic_hash == "0xproof"

        # The context manager usage is verified through the mock_subtensor_cls behavior
        mock_subtensor_cls.return_value.__enter__.assert_called_once()

    def test_wraps_generic_exception_as_click_exception(self):
        from unittest.mock import patch

        keypair = Mock()
        keypair.ss58_address = "5Addr"

        with patch(
            "qbittensor.cli.miner.tao_transfer.bt.Subtensor"
        ) as mock_subtensor_cls:
            mock_subtensor_cls.return_value.__enter__.side_effect = RuntimeError("network down")
            with pytest.raises(click.ClickException, match="Fee payment transaction failed"):
                transfer_tao_for_submission(
                    console=silent_console(),
                    source_ss58="5Addr",
                    keypair=keypair,
                    network="finney",
                    milestone_id="m-err",
                    fee_tao=0.5,
                    miner_hotkey="5H",
                    upload_endpoint_id="u-1",
                )
