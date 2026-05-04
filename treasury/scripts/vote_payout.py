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
Vote on payout proposal (Validators)
"""

import argparse
import subprocess
import sys
import os
import requests

from utils.common import parse_oz_custom_error
try:
    from substrateinterface import SubstrateInterface, Keypair
except ImportError:
    sys.exit("Please install: pip install substrate-interface")

API_BASE_URL = f"{os.getenv('CHALLENGES_API_URL', 'https://challenges.qbittensorlabs.com')}/v1"
ALPHA_LIMIT = 25000 # Must match the limit used in your propose script
STAKING_V2_ADDRESS = "0x0000000000000000000000000000000000000805"

def hash_proposal(contract, targets, values, calldatas, desc, rpc):
    desc_cmd = ["cast", "keccak", desc]
    desc_hash = subprocess.run(desc_cmd, capture_output=True, text=True).stdout.strip()

    hash_cmd = [
        "cast", "call", contract,
        "hashProposal(address[],uint256[],bytes[],bytes32)(uint256)",
        f"[{targets}]", f"[{values}]", f"[{calldatas}]", desc_hash,
        "--rpc-url", rpc
    ]
    res = subprocess.run(hash_cmd, capture_output=True, text=True)
    if res.returncode == 0:
        return res.stdout.strip().split()[0]
    else:
        print(f"❌ Failed to hash proposal: {res.stderr}")
        sys.exit(1)

def fetch_challenges():
    try:
        res = requests.get(f"{API_BASE_URL}/challenges")
        res.raise_for_status()
        base_challenges = res.json().get('challenges', [])
        
        detailed_challenges = []
        for c in base_challenges:
            c_id = c['id']
            detail_res = requests.get(f"{API_BASE_URL}/challenges/{c_id}")
            if detail_res.status_code == 200:
                detailed_challenges.append(detail_res.json())
        return detailed_challenges
    except Exception as e:
        print(f"❌ Failed to fetch challenges: {e}")
        sys.exit(1)

def interactive_picker():
    challenges = fetch_challenges()
    if not challenges:
        print("No challenges found.")
        sys.exit(0)

    options = []
    for c in challenges:
        for m in c.get('milestones', []):
            if m.get('status', '').upper() == 'COMPLETE':
                completed_at = m.get('completed_at') or m.get('completedAt') or 'Unknown'
                options.append({'c_name': c['name'], 'm_name': m['name'], 'id': m['id'], 'prizeAlpha': m.get('prizeAlpha'), 'solved_address': m.get('solved_address'), 'completed_at': completed_at})
    
    if not options:
        print("No completed milestones available for payout.")
        sys.exit(0)

    print("\n--- Completed Milestones ---")
    for idx, opt in enumerate(options):
        print(f"[{idx}] {opt['c_name']} - {opt['m_name']} (ID: {opt['id']}, Prize: {opt['prizeAlpha']} Alpha, Completed: {opt['completed_at']})")
    
    choice = input("\nSelect a milestone index to pay out: ")
    try:
        selected = options[int(choice)]
        prize_alpha_raw = selected.get('prizeAlpha')
        if prize_alpha_raw is None or float(prize_alpha_raw) <= 0:
            print("❌ Milestone does not have a valid prizeAlpha set.")
            sys.exit(1)
        if not selected.get('solved_address'):
            print("❌ Milestone does not have a solved_address set.")
            sys.exit(1)
        return selected['id'], float(prize_alpha_raw), f"{selected['c_name']} - {selected['m_name']}", selected['solved_address']
    except (IndexError, ValueError):
        print("Invalid selection.")
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Vote on a Payout proposal by verifying with the API first.")
    parser.add_argument("--contract", required=True, help="Treasury contract address")
    parser.add_argument("--milestone-id", help="Direct milestone ID (skips interactive picker)")
    parser.add_argument("--retry", type=int, default=0, help="Retry counter used in the proposal description (e.g. 1)")
    parser.add_argument("--vault-hotkey", required=True, help="Vault hotkey (hex)")
    parser.add_argument("--netuid", type=int, required=True, help="The subnet netuid")
    parser.add_argument("--support", required=True, type=str, choices=['true', 'false'], help="'false' = Against, 'true' = For")
    parser.add_argument("--pk", required=True, help="Validator Private Key")
    parser.add_argument("--rpc", required=True, help="RPC URL")
    args = parser.parse_args()

    milestone_id = args.milestone_id
    prizeAlpha = 0
    desc_prefix = ""
    solved_address = None
    
    if not milestone_id:
        milestone_id, prizeAlpha, desc_prefix, solved_address = interactive_picker()
    else:
        print("\n📡 Fetching Milestone Data from API...")
        challenges = fetch_challenges()
        found = False
        
        for c in challenges:
            for m in c.get('milestones', []):
                if m['id'] == milestone_id:
                    prizeAlpha = m.get('prizeAlpha')
                    desc_prefix = f"{c['name']} - {m['name']}"
                    solved_address = m.get('solved_address')
                    found = True
                    break
            if found: break
        
        if not found:
            print(f"❌ Milestone {milestone_id} not found in API.")
            sys.exit(1)
            
        if prizeAlpha is None or float(prizeAlpha) <= 0:
            print(f"❌ Milestone {milestone_id} does not have a valid prizeAlpha.")
            sys.exit(1)
            
        prizeAlpha = float(prizeAlpha)

        if not solved_address:
            print(f"❌ Milestone {milestone_id} does not have a solved_address set.")
            sys.exit(1)

    # Look up coldkey
    print(f"🔍 Looking up coldkey for hotkey: {solved_address}")
    try:
        substrate_url = args.rpc.replace("http://", "ws://").replace("https://", "wss://")
        substrate = SubstrateInterface(url=substrate_url)
        owner = substrate.query("SubtensorModule", "Owner", [solved_address])
        if not owner.value or owner.value == "5C4hrfjw9DjXZTzV3MwzrrAr9P1MJhSrvWGWqi1eSuyUpnhM":
            print(f"❌ Could not find owner (coldkey) for hotkey {solved_address} on chain.")
            sys.exit(1)
        miner_coldkey = "0x" + Keypair(ss58_address=owner.value).public_key.hex()
    except Exception as e:
        print(f"❌ Error querying chain for coldkey: {e}")
        sys.exit(1)

    # Convert vault_hotkey to hex if it is an SS58 address
    vault_hotkey_hex = args.vault_hotkey
    if not vault_hotkey_hex.startswith("0x"):
        vault_hotkey_hex = "0x" + Keypair(ss58_address=vault_hotkey_hex).public_key.hex()

    chunks = []
    remaining = prizeAlpha
    while remaining > 0:
        chunk = min(remaining, ALPHA_LIMIT)
        chunks.append(chunk)
        remaining -= chunk

    print(f"\nVoting on {len(chunks)} parts...")
    support_val = 1 if args.support.lower() == 'true' else 0

    for i, target_amount in enumerate(chunks):
        part_num = i + 1
        print(f"\n--- Processing Part {part_num} of {len(chunks)} ---")
        # Alpha transfers use 9 decimals (RAO) for Bittensor precompiles, unlike standard 18-decimal EVM tokens
        exact_amount_rao = str(int(target_amount * (10**9)))
        exact_desc = desc_prefix
        if args.retry > 0:
            exact_desc += f" (Retry {args.retry})"
        if len(chunks) > 1:
            exact_desc += f" - Part {part_num} of {len(chunks)}"
        
        calldata_cmd = [
            "cast", "calldata", 
            "transferStake(bytes32,bytes32,uint256,uint256,uint256)",
            miner_coldkey, vault_hotkey_hex, str(args.netuid), str(args.netuid), exact_amount_rao
        ]
        calldata_raw = subprocess.run(calldata_cmd, capture_output=True, text=True).stdout.strip()

        print(f"🧮 Calculating Expected Proposal Hash...")
        prop_id = hash_proposal(args.contract, STAKING_V2_ADDRESS, "0", calldata_raw, exact_desc, args.rpc)
        
        print(f"   ↳ Proposal ID: {prop_id}")

        # Pre-flight check: Verify the proposal is active
        state_cmd = [
            "cast", "call", args.contract,
            "state(uint256)(uint8)", prop_id,
            "--rpc-url", args.rpc
        ]
        state_res = subprocess.run(state_cmd, capture_output=True, text=True)
        
        if state_res.returncode != 0:
            print(f"❌ Proposal {prop_id} not found or error querying state on {args.contract}.")
            continue
            
        try:
            state_val = int(state_res.stdout.strip().split()[0])
            if state_val != 1:
                states = {0: "Pending", 1: "Active", 2: "Canceled", 3: "Defeated", 4: "Succeeded", 5: "Queued", 6: "Expired", 7: "Executed"}
                state_name = states.get(state_val, "Unknown")
                print(f"❌ Cannot vote: Proposal is '{state_name}' (State {state_val}) on {args.contract}.")
                print("   ↳ You can only vote on 'Active' proposals.")
                continue
        except ValueError:
            pass 

        cmd = [
            "cast", "send", args.contract,
            "castVote(uint256,uint8)",
            prop_id, str(support_val),
            "--private-key", args.pk,
            "--rpc-url", args.rpc
        ]

        print(f"🗳️  Casting vote {args.support} (value {support_val}) for payout...")
        res = subprocess.run(cmd, capture_output=True, text=True)
        
        if res.returncode == 0:
            print("✅ Vote cast successfully!")
        else:
            err_out = res.stderr.strip() or res.stdout.strip()
            parsed_err = parse_oz_custom_error(err_out)
            print(f"❌ Failed to cast vote: \n   ↳ {parsed_err}")

            if "GovernorAlreadyCastVote" in parsed_err:
                print("   ↳ You have already voted on this part. Continuing...")
                continue
            elif "gas required exceeds allowance" in err_out:
                print("   ↳ ERROR: Gas estimation failed. This usually means:")
                print("            1. Your EVM account (derived from --pk) has 0 TAO balance.")
                print("            2. The transaction reverted (e.g., you are not an active, trusted validator).")
            sys.exit(1)

if __name__ == "__main__":
    main()