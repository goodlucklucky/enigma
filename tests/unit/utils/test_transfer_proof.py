"""
Solid unit tests for the fee payment verification logic.

These tests focus on the batch + remark parsing and verification helpers
used for fee payments. The fee coldkey signs a Utility.batch_all containing
the transfer + a remark with the binding data.

There is no legacy plain-transfer proof format.
"""

from unittest.mock import MagicMock, Mock

import pytest

from qbittensor.utils.transfer_proof import (
    _find_remark_data_in_calls,
    _find_transfer_in_calls,
    _get_calls_from_batch,
    _verify_batch_fee_payment_on_chain,
    parse_fee_binding_remark,
    FEE_BINDING_REMARK_VERSION,
)


def make_mock_call(module: str, function: str, args: dict) -> dict:
    return {
        "call_module": module,
        "call_function": function,
        "call_args": [{"name": k, "value": v} for k, v in args.items()],
    }


class TestBatchParsingHelpers:
    def test_get_calls_from_batch(self):
        # Use object with .value to match real substrate decoded extrinsics
        batch_ex = MagicMock()
        batch_ex.value = {
            "call": {
                "call_module": "Utility",
                "call_function": "batch_all",
                "call_args": [
                    {
                        "name": "calls",
                        "value": [
                            make_mock_call("Balances", "transfer_keep_alive", {"dest": "5D..", "value": 123}),
                            make_mock_call("System", "remark_with_event", {"remark": b"hello"}),
                        ],
                    }
                ],
            }
        }
        calls = _get_calls_from_batch(batch_ex)
        assert len(calls) == 2

    def test_find_transfer_in_calls_success(self):
        calls = [
            make_mock_call("System", "remark", {"remark": b"foo"}),
            make_mock_call("Balances", "transfer_keep_alive", {"dest": {"Id": b"\x00" * 32}, "value": 424242}),
        ]
        result = _find_transfer_in_calls(calls)
        assert result is not None
        dest, value = result
        assert value == 424242

    def test_find_remark_data_in_calls(self):
        remark_bytes = f"{FEE_BINDING_REMARK_VERSION}\nminer_hotkey:5H..".encode()
        calls = [
            make_mock_call("Balances", "transfer_keep_alive", {"dest": "x", "value": 1}),
            make_mock_call("System", "remark_with_event", {"remark": remark_bytes}),
        ]
        data = _find_remark_data_in_calls(calls)
        assert data == remark_bytes

    def test_parse_fee_binding_remark(self):
        raw = f"""{FEE_BINDING_REMARK_VERSION}
miner_hotkey:5Hotkey123
milestone_id:milestone-xyz
upload_endpoint_id:upload-abc123
amount_rao:424242
extra:ignored""".encode()

        parsed = parse_fee_binding_remark(raw)
        assert parsed["miner_hotkey"] == "5Hotkey123"
        assert parsed["milestone_id"] == "milestone-xyz"
        assert parsed["upload_endpoint_id"] == "upload-abc123"
        assert parsed["amount_rao"] == "424242"
        assert "extra" not in parsed


class TestBatchFeeVerification:
    def test_successful_batch_with_matching_remark(self):
        substrate = MagicMock()
        substrate.ss58_encode.side_effect = lambda x: "5Coldkey" if isinstance(x, (bytes, bytearray)) else str(x)

        # Build a fake successful receipt
        receipt = Mock()
        receipt.is_success = True
        receipt.error_message = None

        remark_data = f"{FEE_BINDING_REMARK_VERSION}\nminer_hotkey:5Hot\nmilestone_id:m1\nupload_endpoint_id:u1\namount_rao:12345".encode()

        # Wrap in an object with .value so the existing _signer_ss58 and _get_call_dict helpers work
        batch_ex = MagicMock()
        batch_ex.value = {
            "address": "5Coldkey",
            "call": {
                "call_module": "Utility",
                "call_function": "batch_all",
                "call_args": [
                    {
                        "name": "calls",
                        "value": [
                            make_mock_call("Balances", "transfer_keep_alive", {
                                "dest": "5D82xX2p14X7gCGKu2Hpf8feNAzeXefgoeh4UJgVRpVTbVP4",
                                "value": 12345,
                            }),
                            make_mock_call("System", "remark_with_event", {"remark": remark_data}),
                        ],
                    }
                ],
            },
        }

        receipt.extrinsic = batch_ex
        substrate.retrieve_extrinsic_by_hash.return_value = receipt

        ok, err = _verify_batch_fee_payment_on_chain(
            substrate=substrate,
            block_hash="0xblock",
            extrinsic_hash="0xtx",
            expected_signer_ss58="5Coldkey",
            expected_dest_ss58="5D82xX2p14X7gCGKu2Hpf8feNAzeXefgoeh4UJgVRpVTbVP4",
            expected_value_rao=12345,
        )

        assert ok is True, f"Expected success but got: {err}"
        assert err == ""

    def test_rejects_wrong_amount(self):
        substrate = MagicMock()
        substrate.ss58_encode.side_effect = lambda x: "5Coldkey" if isinstance(x, (bytes, bytearray)) else str(x)

        receipt = Mock(is_success=True, error_message=None)

        remark_data = f"{FEE_BINDING_REMARK_VERSION}\namount_rao:999".encode()

        batch_ex = MagicMock()
        batch_ex.value = {
            "address": "5Coldkey",
            "call": {
                "call_module": "Utility",
                "call_function": "batch_all",
                "call_args": [{
                    "name": "calls",
                    "value": [
                        make_mock_call("Balances", "transfer_keep_alive", {
                            "dest": "5D82xX2p14X7gCGKu2Hpf8feNAzeXefgoeh4UJgVRpVTbVP4",
                            "value": 12345,
                        }),
                        make_mock_call("System", "remark_with_event", {"remark": remark_data}),
                    ]
                }]
            }
        }
        receipt.extrinsic = batch_ex
        substrate.retrieve_extrinsic_by_hash.return_value = receipt

        ok, err = _verify_batch_fee_payment_on_chain(
            substrate=substrate,
            block_hash="0xb",
            extrinsic_hash="0xt",
            expected_signer_ss58="5Coldkey",
            expected_dest_ss58="5D82xX2p14X7gCGKu2Hpf8feNAzeXefgoeh4UJgVRpVTbVP4",
            expected_value_rao=12345,
        )

        assert ok is False
        assert "amount" in err.lower() or "value" in err.lower()

    def test_rejects_malformed_remark(self):
        """Adversarial test: remark exists but is garbage."""
        substrate = MagicMock()
        substrate.ss58_encode.side_effect = lambda x: "5Coldkey" if isinstance(x, (bytes, bytearray)) else str(x)

        receipt = Mock(is_success=True, error_message=None)

        garbage = b"this is not a valid binding remark at all"

        batch_ex = MagicMock()
        batch_ex.value = {
            "address": "5Coldkey",
            "call": {
                "call_module": "Utility",
                "call_function": "batch_all",
                "call_args": [{
                    "name": "calls",
                    "value": [
                        make_mock_call("Balances", "transfer_keep_alive", {
                            "dest": "5D82xX2p14X7gCGKu2Hpf8feNAzeXefgoeh4UJgVRpVTbVP4",
                            "value": 12345,
                        }),
                        make_mock_call("System", "remark_with_event", {"remark": garbage}),
                    ]
                }]
            }
        }
        receipt.extrinsic = batch_ex
        substrate.retrieve_extrinsic_by_hash.return_value = receipt

        ok, err = _verify_batch_fee_payment_on_chain(
            substrate=substrate,
            block_hash="0xb",
            extrinsic_hash="0xt",
            expected_signer_ss58="5Coldkey",
            expected_dest_ss58="5D82xX2p14X7gCGKu2Hpf8feNAzeXefgoeh4UJgVRpVTbVP4",
            expected_value_rao=12345,
        )

        assert ok is False
        assert "version" in err.lower() or "remark" in err.lower()
