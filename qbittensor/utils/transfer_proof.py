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

"""Canonical transfer↔miner binding for synapse verification (off-chain + on-chain)."""

from __future__ import annotations

from bittensor_wallet import Keypair

from qbittensor.dto.challenge import TransferProof


TRANSFER_DEST_SS58 = "5D82xX2p14X7gCGKu2Hpf8feNAzeXefgoeh4UJgVRpVTbVP4"

try:
    from substrateinterface.exceptions import ExtrinsicNotFound
except ImportError:  # pragma: no cover
    ExtrinsicNotFound = type("ExtrinsicNotFound", (Exception,), {})

TRANSFER_PROOF_VERSION = "quantum-innovate/transfer-proof/v1"


def build_transfer_proof_message(
    *,
    miner_hotkey: str,
    milestone_id: str,
    upload_id: str,
    tx_hash: str,
    transfer_from_ss58: str,
    transfer_to_ss58: str,
    transfer_amount_rao: str,
) -> str:
    """Deterministic UTF-8 message signed by the miner hotkey."""
    tx = (tx_hash or "").strip().lower()
    if tx.startswith("0x"):
        tx = tx[2:]
    lines = [
        TRANSFER_PROOF_VERSION,
        f"miner_hotkey:{miner_hotkey}",
        f"milestone_id:{milestone_id}",
        f"upload_id:{upload_id}",
        f"tx_hash:{tx}",
        f"from:{transfer_from_ss58}",
        f"to:{transfer_to_ss58}",
        f"amount_rao:{transfer_amount_rao}",
    ]
    return "\n".join(lines)


def _normalize_0x_hash(hex_hash: str) -> str:
    h = (hex_hash or "").strip().lower()
    if h.startswith("0x"):
        h = h[2:]
    return "0x" + h


def _call_value_as_dict(call: Any) -> dict[str, Any]:
    if call is None:
        return {}
    if hasattr(call, "value"):
        v = call.value
        if isinstance(v, dict):
            return v
    if isinstance(call, dict):
        return call
    return {}


def _args_as_dict(call_args: Any) -> dict[str, Any]:
    if call_args is None:
        return {}
    if isinstance(call_args, dict):
        return dict(call_args)
    if isinstance(call_args, list):
        out: dict[str, Any] = {}
        for item in call_args:
            if isinstance(item, dict) and "name" in item:
                out[item["name"]] = item.get("value")
        return out
    return {}


def _coerce_int_rao(x: Any) -> int:
    if isinstance(x, int):
        return x
    if isinstance(x, str):
        return int(x)
    if hasattr(x, "value"):
        return int(x.value)
    return int(x)


def _dest_to_ss58(dest: Any, substrate: Any) -> str | None:
    if dest is None:
        return None
    if isinstance(dest, str):
        return dest
    if isinstance(dest, dict) and "Id" in dest:
        inner = dest["Id"]
        if isinstance(inner, str):
            return inner
        if isinstance(inner, (bytes, bytearray)):
            return substrate.ss58_encode(inner)
    if isinstance(dest, (bytes, bytearray)):
        return substrate.ss58_encode(dest)
    return None


def _signer_ss58(extrinsic: Any, substrate: Any) -> str | None:
    ev = getattr(extrinsic, "value", None)
    if not isinstance(ev, dict):
        return None
    addr = ev.get("address")
    if isinstance(addr, str):
        return addr
    if isinstance(addr, (bytes, bytearray)):
        return substrate.ss58_encode(addr)
    if isinstance(addr, dict):
        return _dest_to_ss58(addr, substrate)
    return None


def _get_call_dict_from_extrinsic(extrinsic: Any) -> dict[str, Any] | None:
    ev = getattr(extrinsic, "value", None)
    if not isinstance(ev, dict):
        return None
    cv = _call_value_as_dict(ev.get("call"))
    return cv or None


def _parse_balances_transfer_keep_alive(extrinsic: Any) -> tuple[Any, int] | None:
    cv = _get_call_dict_from_extrinsic(extrinsic)
    if not cv:
        return None
    if cv.get("call_module") != "Balances":
        return None
    if cv.get("call_function") != "transfer_keep_alive":
        return None
    args = _args_as_dict(cv.get("call_args"))
    dest = args.get("dest")
    raw_val = args.get("value")
    if raw_val is None:
        return None
    return dest, _coerce_int_rao(raw_val)


def _verify_transfer_extrinsic_on_chain(
    *,
    substrate: Any,
    block_hash: str,
    extrinsic_hash: str,
    expected_signer_ss58: str,
    expected_dest_ss58: str,
    expected_value_rao: int,
) -> tuple[bool, str]:
    try:
        receipt = substrate.retrieve_extrinsic_by_hash(
            _normalize_0x_hash(block_hash),
            _normalize_0x_hash(extrinsic_hash),
        )
        receipt.retrieve_extrinsic()
    except ExtrinsicNotFound:
        return False, "transfer extrinsic not found for block_hash + extrinsic_hash"
    except Exception as e:
        return False, f"on-chain extrinsic lookup failed: {e}"

    if not receipt.is_success:
        return False, f"transfer extrinsic did not succeed: {receipt.error_message}"

    # Compatibility: some substrate-interface versions expose `.extrinsic`,
    # others only populate a private field after `retrieve_extrinsic()`.
    ex = getattr(receipt, "extrinsic", None)
    if ex is None:
        ex = getattr(receipt, "_ExtrinsicReceipt__extrinsic", None)
    if ex is None:
        try:
            idx = getattr(receipt, "extrinsic_idx", None)
            block = substrate.get_block(block_hash=_normalize_0x_hash(block_hash))
            extrinsics = block.get("extrinsics") if isinstance(block, dict) else None
            if isinstance(extrinsics, list) and isinstance(idx, int) and 0 <= idx < len(extrinsics):
                ex = extrinsics[idx]
        except Exception:
            ex = None
    if ex is None:
        return False, "could not decode transfer extrinsic from receipt"
    signer = _signer_ss58(ex, substrate)
    if not signer:
        return False, "could not decode extrinsic signer"
    if signer != expected_signer_ss58:
        return (
            False,
            f"extrinsic signer ({signer}) does not match transfer_from_ss58 ({expected_signer_ss58})",
        )

    parsed = _parse_balances_transfer_keep_alive(ex)
    if parsed is None:
        return False, "extrinsic call is not Balances.transfer_keep_alive"
    dest_raw, value_rao = parsed
    dest_ss58 = _dest_to_ss58(dest_raw, substrate)
    if dest_ss58 != expected_dest_ss58:
        return (
            False,
            f"on-chain transfer dest ({dest_ss58}) != expected ({expected_dest_ss58})",
        )
    if value_rao != expected_value_rao:
        return (
            False,
            f"on-chain transfer amount ({value_rao} rao) != expected fee ({expected_value_rao} rao)",
        )

    return True, ""


def verify_transfer_proof_for_synapse(
    proof: TransferProof,
    miner_hotkey_ss58: str,
    subtensor,
    expected_transfer_amount_rao: str,
) -> tuple[bool, str]:
    """
    Validate a transfer proof for a solution submission.

    Expects a TransferProof instance (typically created via
    TransferProof.from_platform_submission() for cross-checks, or constructed
    directly from a SolutionSynapse in the normal validation path).
    """
    cand = proof.solution_candidate
    if cand is None:
        return False, "no solution_candidate"

    tx_hash = proof.tx_hash.strip()
    block_hash = proof.transfer_block_hash.strip()
    t_from = proof.transfer_from_ss58.strip()
    t_to = proof.transfer_to_ss58.strip()
    amount_rao = proof.transfer_amount_rao.strip()
    msg = proof.transfer_proof_message.strip()
    sig_hex = proof.transfer_proof_signature_hex.strip()

    if not tx_hash:
        return False, "missing tx_hash"
    if not block_hash:
        return (
            False,
            "missing transfer_block_hash (re-run mine_enigma CLI so inclusion block is stored)",
        )
    if not t_from:
        return False, "missing transfer_from_ss58"
    if not t_to:
        return False, "missing transfer_to_ss58"
    if not amount_rao:
        return False, "missing transfer_amount_rao"
    if not msg:
        return False, "missing transfer_proof_message"
    if not sig_hex:
        return False, "missing transfer_proof_signature_hex"

    milestone_id = cand.challenge_milestone_id
    upload_id = cand.upload_endpoint_id

    expected_rao_str = expected_transfer_amount_rao
    if amount_rao != expected_rao_str:
        return (
            False,
            f"transfer_amount_rao must be {expected_rao_str} (priceTao for milestone, got {amount_rao})",
        )
    if t_to != TRANSFER_DEST_SS58:
        return (
            False,
            f"transfer_to_ss58 must be fee destination {TRANSFER_DEST_SS58} (got {t_to})",
        )

    expected = build_transfer_proof_message(
        miner_hotkey=miner_hotkey_ss58,
        milestone_id=milestone_id,
        upload_id=upload_id,
        tx_hash=tx_hash,
        transfer_from_ss58=t_from,
        transfer_to_ss58=t_to,
        transfer_amount_rao=amount_rao,
    )
    if msg != expected:
        return False, "transfer_proof_message does not match canonical payload (possible tampering)"

    try:
        sig_bytes = bytes.fromhex(sig_hex.replace("0x", ""))
    except ValueError:
        return False, "transfer_proof_signature_hex is not valid hex"

    try:
        kp = Keypair(ss58_address=miner_hotkey_ss58)
    except Exception as e:
        return False, f"could not load keypair for miner hotkey: {e}"

    if not kp.verify(msg.encode("utf-8"), sig_bytes):
        return False, "hotkey signature verification failed"

    owner = subtensor.get_hotkey_owner(miner_hotkey_ss58)
    if owner is None:
        return (
            False,
            "miner hotkey has no on-chain owner (hotkey not registered?); "
            "cannot bind transfer sender to miner coldkey",
        )

    owner_ss58 = owner if isinstance(owner, str) else getattr(owner, "value", owner)
    if not isinstance(owner_ss58, str):
        owner_ss58 = str(owner_ss58)
    if owner_ss58 != t_from:
        return (
            False,
            f"transfer_from_ss58 ({t_from}) does not match coldkey owner ({owner_ss58}) "
            f"for miner hotkey {miner_hotkey_ss58}",
        )

    substrate = getattr(subtensor, "substrate", None)
    if substrate is None:
        return False, "subtensor.substrate is not available for on-chain verification"

    ok, err = _verify_transfer_extrinsic_on_chain(
        substrate=substrate,
        block_hash=block_hash,
        extrinsic_hash=tx_hash,
        expected_signer_ss58=t_from,
        expected_dest_ss58=TRANSFER_DEST_SS58,
        expected_value_rao=int(expected_rao_str),
    )
    if not ok:
        return False, err

    return True, ""
