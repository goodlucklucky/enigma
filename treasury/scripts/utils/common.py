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

import os
import sys
import argparse
from web3 import Web3

# Public lite RPC endpoint for Bittensor EVM (mainnet)
DEFAULT_RPC_URL = "https://lite.chain.opentensor.ai"

OZ_CUSTOM_ERRORS = {
    "0x31b75e4d": "GovernorUnexpectedProposalState (Action not allowed in current state)",
    "0x41e17e47": "GovernorAlreadyCastVote (You have already voted on this proposal)",
    "0x86bb51b8": "AccessControlUnauthorizedAccount (Missing required role/permissions)",
    "0x1425ea42": "FailedInnerCall (Execution reverted, likely Vault has insufficient funds)",
    "0x0ebed1a3": "GovernorOnlyExecutor (Only the designated executor can call this)",
    "0x6002b8b9": "GovernorNonexistentProposal (Proposal ID does not exist)",
    "0xc2df61b0": "GovernorOnlyProposer (Only a trusted proposer can call this)",
    "0xd24bfa1e": "FailedCall (Target contract reverted the execution)",
    "0x38e7d230": "TimelockUnexpectedOperationState (Timelock delay hasn't expired yet)",
    "0x51c6c547": "GovernorInvalidVoteType (Invalid vote option selected)",
    "0xb8b6a382": "GovernorAlreadyQueuedProposal (Proposal is already in the Timelock)"
}


def add_web3_arguments(parser: argparse.ArgumentParser, requires_private_key: bool = True):
    """Adds standard Web3 arguments to the parser."""
    parser.add_argument(
        "--rpc-url",
        default=DEFAULT_RPC_URL,
        help=f"RPC endpoint URL (default: {DEFAULT_RPC_URL})"
    )
    if requires_private_key:
        parser.add_argument("--private-key", default=None, help="Private key (or set PRIVATE_KEY env var)")
        parser.add_argument("--force-gas-price-gwei", type=float, help="Force a specific Gas Price in Gwei")


def setup_web3_connection(rpc_url: str):
    """Establishes Web3 connection."""
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        raise ConnectionError(f"Failed to connect to RPC URL: {rpc_url}")
    return w3


def get_account(w3: Web3, private_key_arg: str = None):
    """Loads account from argument or environment variable."""
    private_key = private_key_arg or os.getenv("PRIVATE_KEY")
    if not private_key:
        raise SystemExit("Error: Set PRIVATE_KEY env var or pass --private-key")

    try:
        account = w3.eth.account.from_key(private_key)
        return account
    except Exception as e:
        raise SystemExit(f"Invalid Private Key: {e}")


def setup_web3_with_account(args):
    """Helper to setup both Web3 and Account from parsed args."""
    try:
        w3 = setup_web3_connection(args.rpc_url)
        account = get_account(w3, args.private_key)
        return w3, account
    except Exception as e:
        print(f"CRITICAL ERROR: {e}", file=sys.stderr)
        sys.exit(1)


def parse_oz_custom_error(err_out: str) -> str:
    """Extracts and formats known OpenZeppelin custom errors from cast output."""
    reason = err_out.split("revert ")[-1].split(", data:")[0].strip() if "revert " in err_out else err_out

    if not reason and "data:" in err_out:
        hex_data = err_out.split('data:')[-1].strip().replace('"', '')
        selector = hex_data[:10].lower()

        if selector in OZ_CUSTOM_ERRORS:
            error_name = OZ_CUSTOM_ERRORS[selector]

            if selector == "0x31b75e4d" and len(hex_data) >= 138:
                try:
                    state_val = int(hex_data[74:138], 16)
                    states = {0: "Pending", 1: "Active", 2: "Canceled", 3: "Defeated", 4: "Succeeded", 5: "Queued", 6: "Expired", 7: "Executed"}
                    return f"Contract Error: {error_name}\n   ↳ Proposal is currently in state: {state_val} ({states.get(state_val, 'Unknown')})"
                except Exception:
                    pass
            return f"Contract Error: {error_name}"
        return f"Contract Custom Error {hex_data}"

    return reason or err_out
