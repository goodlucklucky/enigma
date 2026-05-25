#!/usr/bin/env python3

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
Vote on whitelist update proposal (Validators)
"""

import argparse
import subprocess
import sys

from utils.common import parse_oz_custom_error

def main():
    parser = argparse.ArgumentParser(description="Vote on a whitelist proposal.")
    parser.add_argument("--contract", required=True, help="Treasury contract address")
    parser.add_argument("--proposal-id", required=True, help="The Proposal ID")
    parser.add_argument("--support", required=True, type=str, choices=['true', 'false'], help="'false' = Against, 'true' = For")
    parser.add_argument("--pk", required=True, help="Validator Private Key")
    parser.add_argument("--rpc", required=True, help="RPC URL")
    args = parser.parse_args()

    contract = args.contract
    prop_id = args.proposal_id

    print(f"📋 Preparing Whitelist Vote:")
    print(f"   Contract   : {contract}")
    print(f"   Proposal ID: {prop_id}")
    print(f"   Support    : {args.support}")

    # Pre-flight check: Verify the proposal is active
    state_cmd = [
        "cast", "call", contract,
        "state(uint256)(uint8)", prop_id,
        "--rpc-url", args.rpc
    ]
    state_res = subprocess.run(state_cmd, capture_output=True, text=True)

    if state_res.returncode != 0:
        print(f"❌ Proposal {prop_id} not found or error querying state on {contract}.")
        sys.exit(1)

    try:
        state_val = int(state_res.stdout.strip().split()[0])
        if state_val != 1:
            states = {0: "Pending", 1: "Active", 2: "Canceled", 3: "Defeated", 4: "Succeeded", 5: "Queued", 6: "Expired", 7: "Executed"}
            state_name = states.get(state_val, "Unknown")
            print(f"❌ Cannot vote: Proposal is '{state_name}' (State {state_val}) on {contract}.")
            print("   ↳ You can only vote on 'Active' proposals.")
            sys.exit(1)
    except ValueError:
        pass

    support_val = 1 if args.support.lower() == 'true' else 0
    cmd = [
        "cast", "send", contract,
        "castVote(uint256,uint8)",
        prop_id, str(support_val),
        "--private-key", args.pk,
        "--rpc-url", args.rpc
    ]

    print(f"\n🗳️  Casting vote {args.support} (value {support_val}) for whitelist proposal...")
    res = subprocess.run(cmd, capture_output=True, text=True)

    if res.returncode == 0:
        print("✅ Vote cast successfully!")
    else:
        err_out = res.stderr.strip() or res.stdout.strip()
        parsed_err = parse_oz_custom_error(err_out)
        print(f"❌ Failed to cast vote: \n   ↳ {parsed_err}")

        if "GovernorAlreadyCastVote" in parsed_err:
            print("   ↳ You have already voted on this proposal.")
            sys.exit(0)
        elif "gas required exceeds allowance" in err_out:
            print("   ↳ ERROR: Gas estimation failed. This usually means:")
            print("            1. Your EVM account (derived from --pk) has 0 TAO balance.")
            print("            2. The transaction reverted (e.g., you are not an active, trusted validator).")
        sys.exit(1)

if __name__ == "__main__":
    main()
