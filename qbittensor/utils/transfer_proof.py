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
import os
from typing import Any

from bittensor_wallet import Keypair

from qbittensor.dto.challenge import TransferProof


TRANSFER_DEST_SS58 = "5D82xX2p14X7gCGKu2Hpf8feNAzeXefgoeh4UJgVRpVTbVP4"

try:
    from substrateinterface.exceptions import ExtrinsicNotFound
except ImportError:  # pragma: no cover
    ExtrinsicNotFound = type("ExtrinsicNotFound", (Exception,), {})

import bittensor as bt

TRANSFER_PROOF_VERSION = "quantum-innovate/transfer-proof/v1"

# Official Bittensor archive endpoint. Lite nodes (the default for most users) prune
# historical state, causing "State discarded" errors on retrieve_extrinsic_by_hash for
# older blocks. We fall back to this (or a user-provided archive) automatically.
ARCHIVE_CHAIN_ENDPOINT = os.environ.get(
    "ENIGMA_ARCHIVE_ENDPOINT", "wss://archive.chain.opentensor.ai:443"
)

# Module-level cache so we don't re-instantiate the archive subtensor on every verification.
_ARCHIVE_SUBSTRATE: Any = None


def _get_archive_substrate(archive_endpoint: str | None = None) -> Any | None:
    """Lazily connect to a Bittensor archive node for historical lookups.

    Using an archive allows retrieve_extrinsic_by_hash / get_block on very old blocks
    that have been pruned on regular "lite" nodes (the default for most operators).
    We prefer (in order):
      1. Explicit archive_endpoint passed by caller / config
      2. ENIGMA_ARCHIVE_ENDPOINT env var
      3. The official public archive (wss://archive.chain.opentensor.ai:443)

    This is the main "other mechanism" besides the Subscan public indexer fallback.
    """
    global _ARCHIVE_SUBSTRATE
    endpoint = archive_endpoint or ARCHIVE_CHAIN_ENDPOINT
    # If we already have a cached one and the endpoint matches the one we want, reuse.
    # For simplicity we keep a single cache (most users will use the default archive).
    if _ARCHIVE_SUBSTRATE is not None:
        return _ARCHIVE_SUBSTRATE

    try:
        # In bittensor >= v9/v10 the preferred way is to pass archive_endpoints.
        # This tells the Subtensor to use these for historical queries (retrieve_extrinsic_by_hash,
        # get_block on old blocks, etc.) when the primary lite endpoint can't serve them.
        archive_subtensor = bt.Subtensor(archive_endpoints=[endpoint])
        _ARCHIVE_SUBSTRATE = archive_subtensor.substrate
        bt.logging.info(
            f"Connected to Bittensor archive endpoint for historical extrinsic verification: {endpoint}"
        )
        return _ARCHIVE_SUBSTRATE
    except Exception as e:
        bt.logging.warning(
            f"Could not connect to Bittensor archive endpoint {endpoint} "
            f"for old tx verification fallback: {e}. Old extrinsics may require the "
            "Subscan indexer fallback or your main subtensor to be an archive node."
        )
        _ARCHIVE_SUBSTRATE = None
        return None


def _fetch_extrinsic_details_via_subscan(extrinsic_hash: str) -> dict | None:
    """Fallback to Subscan public API for historical extrinsic data.

    This is a reliable "other mechanism" / public indexer that maintains full history
    without requiring the caller to use an archive node. Subscan returns decoded calls,
    success status, signer, events, etc.

    Returns a dict normalized enough for our call-parsing helpers (or None on failure).
    """
    # Use stdlib urllib to avoid adding a hard dependency on 'requests'.
    import json
    import urllib.request
    import urllib.error

    url = "https://bittensor.api.subscan.io/api/scan/extrinsic"
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps({"hash": _normalize_0x_hash(extrinsic_hash)}).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            if resp.status != 200:
                return None
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        if payload.get("code") != 0:
            return None
        data = payload.get("data") or {}
        ext = data.get("extrinsic") or data
        if not ext:
            return None
        # Subscan shape is close to what we parse: it has account_id (signer),
        # call_module / call_function or params, success flag, etc.
        # We return the raw-ish dict; our _get_call... helpers are already tolerant of dict forms.
        return ext
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, Exception):
        return None


# Remark version (must match what the CLI puts in the remark)
FEE_BINDING_REMARK_VERSION = "enigma/fee-binding/v1"


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
    # Handle substrate decoded objects
    ev = getattr(extrinsic, "value", None)
    if isinstance(ev, dict):
        addr = ev.get("address")
        if isinstance(addr, str):
            return addr
        if isinstance(addr, (bytes, bytearray)):
            return substrate.ss58_encode(addr)
        if isinstance(addr, dict):
            return _dest_to_ss58(addr, substrate)

    # Handle plain dicts (get_block output, Subscan API responses, etc.)
    if isinstance(extrinsic, dict):
        # Common keys in raw / Subscan data
        for key in ("account_id", "signer", "address", "from"):
            val = extrinsic.get(key)
            if isinstance(val, str):
                return val
            if isinstance(val, (bytes, bytearray)):
                try:
                    return substrate.ss58_encode(val)
                except Exception:
                    pass
            if isinstance(val, dict):
                res = _dest_to_ss58(val, substrate)
                if res:
                    return res
        # Sometimes the extrinsic is nested
        if "extrinsic" in extrinsic and isinstance(extrinsic["extrinsic"], dict):
            return _signer_ss58(extrinsic["extrinsic"], substrate)

    return None


def _get_call_dict_from_extrinsic(extrinsic: Any) -> dict[str, Any] | None:
    ev = getattr(extrinsic, "value", None)
    if isinstance(ev, dict):
        cv = _call_value_as_dict(ev.get("call"))
        if cv:
            return cv

    if isinstance(extrinsic, dict):
        # Direct on the object (common for Subscan top-level or simplified block extrinsics)
        cv = _call_value_as_dict(extrinsic.get("call") or extrinsic)
        if cv and cv.get("call_module"):
            return cv
        # Subscan sometimes uses "params" for the args of the top call
        if "params" in extrinsic or "call_args" in extrinsic:
            return {
                "call_module": extrinsic.get("call_module") or extrinsic.get("module"),
                "call_function": extrinsic.get("call_function") or extrinsic.get("function"),
                "call_args": extrinsic.get("params") or extrinsic.get("call_args"),
            }
        # Nested under "extrinsic"
        if "extrinsic" in extrinsic:
            return _get_call_dict_from_extrinsic(extrinsic["extrinsic"])

    return None


def _get_calls_from_batch(extrinsic: Any) -> list[dict]:
    """
    Return the list of inner calls if this extrinsic is a Utility.batch_all / batch.

    Handles both:
    - substrate-interface decoded objects (have .value)
    - plain dicts (useful for testing)
    """
    # Try the standard path first (works for real decoded extrinsics)
    cv = _get_call_dict_from_extrinsic(extrinsic)
    if not cv:
        # Fallback: the caller might have passed the raw dict already
        if isinstance(extrinsic, dict):
            cv = _call_value_as_dict(extrinsic.get("call"))
        else:
            return []

    if not cv:
        return []

    if cv.get("call_module") not in ("Utility",):
        return []
    if cv.get("call_function") not in ("batch_all", "batch"):
        return []

    args = _args_as_dict(cv.get("call_args"))
    calls = args.get("calls") or []
    if isinstance(calls, list):
        return calls
    return []


def _find_transfer_in_calls(calls: list[dict]) -> tuple[Any, int] | None:
    """Look for a Balances.transfer_keep_alive inside a list of calls."""
    for call in calls:
        if not isinstance(call, dict):
            continue
        module = call.get("call_module") or call.get("module")
        func = call.get("call_function") or call.get("function")
        if module != "Balances" or func != "transfer_keep_alive":
            continue
        args = _args_as_dict(call.get("call_args") or call.get("args"))
        dest = args.get("dest")
        raw_val = args.get("value")
        if raw_val is None:
            continue
        return dest, _coerce_int_rao(raw_val)
    return None


def _find_remark_data_in_calls(calls: list[dict]) -> bytes | None:
    """Look for a System.remark or System.remark_with_event and return its data as bytes."""
    for call in calls:
        if not isinstance(call, dict):
            continue
        module = call.get("call_module") or call.get("module")
        func = call.get("call_function") or call.get("function")
        if module != "System" or func not in ("remark", "remark_with_event"):
            continue
        args = _args_as_dict(call.get("call_args") or call.get("args"))
        remark = args.get("remark") or args.get("data")
        if remark is None:
            continue
        if isinstance(remark, (bytes, bytearray)):
            return bytes(remark)
        if isinstance(remark, str):
            return remark.encode("utf-8")
        # Sometimes substrate returns a list of ints
        if isinstance(remark, (list, tuple)):
            try:
                return bytes(remark)
            except Exception:
                pass
    return None


def parse_fee_binding_remark(remark_bytes: bytes) -> dict[str, str]:
    """
    Parse the canonical remark produced by the CLI.

    Expected format (utf-8 lines):
        enigma/fee-binding/v1
        miner_hotkey:<ss58>
        milestone_id:<id>
        upload_endpoint_id:<id>
        amount_rao:<int>

    Returns a dict with the parsed fields (only known keys).
    """
    try:
        text = remark_bytes.decode("utf-8", errors="replace")
    except Exception:
        return {}

    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key in ("miner_hotkey", "milestone_id", "upload_endpoint_id", "amount_rao"):
            result[key] = value
    return result


def _verify_batch_fee_payment_on_chain(
    *,
    substrate: Any,
    block_hash: str,
    extrinsic_hash: str,
    expected_signer_ss58: str,
    expected_dest_ss58: str,
    expected_value_rao: int,
    expected_remark_prefix: str = FEE_BINDING_REMARK_VERSION,
    archive_endpoint: str | None = None,
) -> tuple[bool, str]:
    """
    Verify that the extrinsic is a successful Utility.batch_all containing:
      - A Balances.transfer_keep_alive to the correct destination and amount
      - A System.remark* whose data starts with the expected version and contains the binding
    """
    receipt = None
    try:
        receipt = substrate.retrieve_extrinsic_by_hash(
            _normalize_0x_hash(block_hash),
            _normalize_0x_hash(extrinsic_hash),
        )
        receipt.retrieve_extrinsic()
    except ExtrinsicNotFound:
        return False, "fee payment extrinsic not found"
    except Exception as e:
        err_str = str(e)
        if "State discarded" in err_str or "archive" in err_str.lower() or "pruned" in err_str.lower():
            # The main (usually lite) node does not have historical state for this old block.
            # Try the official archive endpoint transparently.
            archive_sub = _get_archive_substrate(archive_endpoint=archive_endpoint)
            if archive_sub is not None and archive_sub is not substrate:
                try:
                    receipt = archive_sub.retrieve_extrinsic_by_hash(
                        _normalize_0x_hash(block_hash),
                        _normalize_0x_hash(extrinsic_hash),
                    )
                    receipt.retrieve_extrinsic()
                    # Use archive for any subsequent block/extrinsic decoding in this call
                    substrate = archive_sub
                    bt.logging.info(
                        f"Successfully fell back to archive node for extrinsic {extrinsic_hash} "
                        f"(block {block_hash})"
                    )
                except ExtrinsicNotFound:
                    return False, "fee payment extrinsic not found (even on archive)"
                except Exception as e2:
                    bt.logging.warning(
                        f"Archive fallback also failed for {extrinsic_hash}: {e2}. "
                        "Will try get_block + Subscan if needed."
                    )
                    receipt = None  # fall through to block / API fallbacks below
            else:
                bt.logging.warning(
                    f"Historical on-chain lookup for old extrinsic {extrinsic_hash} (block {block_hash}) "
                    "requires an archive node. Main node returned: State discarded. "
                    "Attempting get_block fallback and public indexer (Subscan)..."
                )
                receipt = None  # will try get_block below
        else:
            return False, f"on-chain extrinsic lookup failed: {e}"

    success = True
    error_message = None
    ex = None

    if receipt is not None:
        success = getattr(receipt, "is_success", True)
        error_message = getattr(receipt, "error_message", None)
        ex = getattr(receipt, "extrinsic", None) or getattr(receipt, "_ExtrinsicReceipt__extrinsic", None)
        if ex is None:
            try:
                idx = getattr(receipt, "extrinsic_idx", None)
                block = substrate.get_block(block_hash=_normalize_0x_hash(block_hash))
                extrinsics = block.get("extrinsics") if isinstance(block, dict) else None
                if isinstance(extrinsics, list) and isinstance(idx, int) and 0 <= idx < len(extrinsics):
                    ex = extrinsics[idx]
            except Exception:
                ex = None
    else:
        # Old block / no receipt path: use Subscan (public indexer with full history) + get_block
        bt.logging.info(
            f"Using public indexer (Subscan) + get_block fallback for historical fee extrinsic {extrinsic_hash}"
        )
        subscan_data = _fetch_extrinsic_details_via_subscan(extrinsic_hash)
        if subscan_data:
            success = subscan_data.get("success", True)
            error_message = subscan_data.get("error") or subscan_data.get("error_message")
            # Normalize Subscan response into a shape our call/signer parsers understand
            # (they expect "address" + "call": {"call_module", "call_function", "call_args"} etc.)
            normalized = {
                "address": subscan_data.get("account_id") or subscan_data.get("signer"),
                "call": {
                    "call_module": subscan_data.get("call_module"),
                    "call_function": subscan_data.get("call_function"),
                    "call_args": subscan_data.get("params") or subscan_data.get("call_args"),
                },
                **subscan_data,
            }
            ex = normalized
            # Also try to enrich with raw block extrinsics if helpful for decoding
            try:
                block = substrate.get_block(block_hash=_normalize_0x_hash(block_hash))
                extrinsics = block.get("extrinsics") if isinstance(block, dict) else None
                if isinstance(extrinsics, list) and not normalized.get("call", {}).get("call_module"):
                    for cand in extrinsics:
                        if isinstance(cand, dict):
                            cv = _call_value_as_dict(cand.get("call") or cand)
                            if cv.get("call_module") in ("Utility",) and cv.get("call_function") in ("batch_all", "batch"):
                                ex = {**normalized, **cand}
                                break
            except Exception:
                pass

        if not success:
            return False, f"fee payment extrinsic did not succeed (via Subscan): {error_message}"

        if ex is None:
            # Pure get_block attempt (may still work on lite nodes for the block data itself)
            try:
                block = substrate.get_block(block_hash=_normalize_0x_hash(block_hash))
                extrinsics = block.get("extrinsics") if isinstance(block, dict) else None
                if isinstance(extrinsics, list):
                    for cand in extrinsics:
                        if isinstance(cand, dict):
                            cv = _call_value_as_dict(cand.get("call") or cand)
                            if cv.get("call_module") in ("Utility",) and cv.get("call_function") in ("batch_all", "batch"):
                                ex = cand
                                break
                    if ex is None and extrinsics:
                        ex = extrinsics[0]
            except Exception:
                ex = None

    if not success:
        return False, f"fee payment extrinsic did not succeed: {error_message}"

    if ex is None:
        return False, "could not decode fee payment extrinsic (tried receipt, get_block, and Subscan)"

    signer = _signer_ss58(ex, substrate)
    if not signer:
        return False, "could not decode extrinsic signer"
    if signer != expected_signer_ss58:
        return False, f"extrinsic signer ({signer}) does not match expected ({expected_signer_ss58})"

    calls = _get_calls_from_batch(ex)
    if not calls:
        return False, "extrinsic is not a Utility.batch_all / batch (required for fee payments)"

    transfer = _find_transfer_in_calls(calls)
    if transfer is None:
        return False, "batch did not contain a Balances.transfer_keep_alive"

    dest_raw, value_rao = transfer
    dest_ss58 = _dest_to_ss58(dest_raw, substrate)
    if dest_ss58 != expected_dest_ss58:
        return False, f"transfer destination ({dest_ss58}) != expected ({expected_dest_ss58})"
    if value_rao != expected_value_rao:
        return False, f"transfer amount ({value_rao}) != expected ({expected_value_rao})"

    remark_data = _find_remark_data_in_calls(calls)
    if remark_data is None:
        return False, "batch did not contain a System.remark / remark_with_event"

    remark_str = remark_data.decode("utf-8", errors="replace")
    if not remark_str.startswith(expected_remark_prefix):
        return False, f"remark does not start with expected version {expected_remark_prefix}"

    # Full remark parsing and validation
    parsed = parse_fee_binding_remark(remark_data)

    # Amount consistency (on-chain transfer vs remark)
    try:
        remark_amount = int(parsed.get("amount_rao", "-1"))
    except Exception:
        remark_amount = -1
    if remark_amount != expected_value_rao:
        return False, f"remark amount_rao ({remark_amount}) does not match on-chain value ({expected_value_rao})"

    # We do not enforce miner_hotkey / milestone / upload here because those are validated
    # at a higher level against the `TransferProof` / `SolutionCandidate` by the caller.
    # The on-chain check's job is to prove the coldkey signed a well-formed binding for *some*
    # submission; the higher-level verifier ties it to the specific claim.

    return True, ""


def verify_transfer_proof_for_synapse(
    proof: TransferProof,
    miner_hotkey_ss58: str,
    subtensor,
    expected_transfer_amount_rao: str,
    *,
    archive_endpoint: str | None = None,
) -> tuple[bool, str]:
    """
    Validate a transfer proof for a solution submission.

    Expects a TransferProof instance (typically created via
    TransferProof.from_platform_submission() for cross-checks, or constructed
    directly from a SolutionSynapse in the normal validation path).

    On-chain lookup of the fee extrinsic (the Utility.batch containing the
    transfer + binding remark) uses the provided subtensor substrate. For old
    blocks this commonly fails on "lite" (pruned) nodes with "State discarded".

    The code automatically falls back to:
      - The official Bittensor archive node (wss://archive.chain.opentensor.ai)
      - substrate.get_block + best-effort extrinsic extraction
      - Subscan public indexer API (https://bittensor.api.subscan.io) as a
        completely different mechanism that does not require any archive node.

    The verified_tx_hashes local cache (populated on first successful verification)
    means most repeated or cross-check lookups avoid the on-chain step entirely.

    You can force a specific archive node for historical lookups by passing
    archive_endpoint=... or by setting the ENIGMA_ARCHIVE_ENDPOINT environment
    variable (takes precedence over the built-in official archive).
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

    substrate = subtensor.substrate
    if substrate is None:
        return False, "subtensor.substrate is not available for on-chain verification"

    # The payment must be a Utility.batch_all containing transfer + remark
    ok, err = _verify_batch_fee_payment_on_chain(
        substrate=substrate,
        block_hash=block_hash,
        extrinsic_hash=tx_hash,
        expected_signer_ss58=t_from,
        expected_dest_ss58=TRANSFER_DEST_SS58,
        expected_value_rao=int(expected_rao_str),
        archive_endpoint=archive_endpoint,
    )
    if not ok:
        return False, err

    return True, ""
