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
Get associated hotkeys and voting power for an EVM validator address.
"""

import argparse
import subprocess
import sys

try:
    from substrateinterface import Keypair
except ImportError:
    sys.exit("Please install: pip install substrate-interface")

BITTENSOR_VOTES_ADDRESS = "0x000000000000000000000000000000000000080D"

def main():
    parser = argparse.ArgumentParser(description="Lookup hotkeys and voting power for an EVM address")
    parser.add_argument("--contract", required=True, help="TreasuryController address")
    parser.add_argument("--evm", required=True, help="Validator EVM address")
    parser.add_argument("--rpc", required=True, help="RPC URL")
    args = parser.parse_args()

    print(f"\n🔍 Looking up EVM Address: {args.evm}")

    # 1. Fetch Target NetUID from the Governor
    cmd_netuid = ["cast", "call", args.contract, "TARGET_NETUID()(uint16)", "--rpc-url", args.rpc]
    res_netuid = subprocess.run(cmd_netuid, capture_output=True, text=True)
    if res_netuid.returncode != 0:
        print(f"❌ Failed to fetch TARGET_NETUID from {args.contract}")
        sys.exit(1)
        
    netuid = res_netuid.stdout.strip()
    print(f"   Target NetUID: {netuid}")

    # 2. Get Associated Hotkeys
    cmd_hk = ["cast", "call", args.contract, "getHotkeysForAddress(address)(bytes32[])", args.evm, "--rpc-url", args.rpc]
    res_hk = subprocess.run(cmd_hk, capture_output=True, text=True)
    
    if res_hk.returncode != 0:
        print(f"❌ Failed to fetch hotkeys: {res_hk.stderr}")
        sys.exit(1)
        
    output = res_hk.stdout.strip()
    if not output or output == "[]":
        print("\n   ⚠️ No hotkeys associated with this EVM address on-chain.")
        print("   Ensure the validator has run the 'associate_evm' extrinsic correctly.")
        sys.exit(0)
        
    clean_output = output.strip("[]").replace('"', '').replace("'", "")
    hex_keys = [k.strip() for k in clean_output.split(",") if k.strip()]
    
    print(f"   Found {len(hex_keys)} associated hotkey(s).")
    
    total_power = 0
    
    # 3. Query Voting Power for each Hotkey
    for i, hk_hex in enumerate(hex_keys, 1):
        if hk_hex.startswith("0x") and len(hk_hex) == 66:
            try:
                kp = Keypair(public_key=bytes.fromhex(hk_hex[2:]), ss58_format=42)
                ss58 = kp.ss58_address
            except Exception:
                ss58 = "Unknown Error decoding SS58"
            
            cmd_power = [
                "cast", "call", BITTENSOR_VOTES_ADDRESS, 
                "getVotingPower(uint16,bytes32)(uint256)", 
                netuid, hk_hex, "--rpc-url", args.rpc
            ]
            res_power = subprocess.run(cmd_power, capture_output=True, text=True)
            
            power = 0
            if res_power.returncode == 0:
                power_str = res_power.stdout.strip().split()[0]
                if power_str.isdigit():
                    power = int(power_str)
            else:
                print(f"     ⚠️ EVM Precompile Failed: {res_power.stderr.strip() or res_power.stdout.strip()}")
                
            # 4. Fallback to direct Substrate query if EVM returns 0 or fails
            if power == 0:
                try:
                    import bittensor as bt
                    ws_url = args.rpc.replace("http://", "ws://").replace("https://", "wss://")
                    sub = bt.Subtensor(network=ws_url)
                    
                    # Try querying known voting power maps
                    ema_obj = None
                    for storage_name in ["VotingPower", "ValidatorVotingPower", "VotingPowerEma"]:
                        try:
                            ema_obj = sub.substrate.query("SubtensorModule", storage_name, [ss58, int(netuid)])
                            if ema_obj is not None: break
                        except Exception:
                            try:
                                ema_obj = sub.substrate.query("SubtensorModule", storage_name, [int(netuid), ss58])
                                if ema_obj is not None: break
                            except Exception:
                                continue
                                
                    if ema_obj is not None:
                        power = ema_obj.value
                        print(f"     🔄 Bypassed EVM: Fetched native power directly from Substrate.")
                    else:
                        print("     ⚠️ Substrate Fallback Failed: Could not find a valid voting power map in SubtensorModule.")
                except Exception as e:
                    print(f"     ⚠️ Substrate Fallback Exception: {e}")
            
            total_power += power
            
            print(f"\n   → Hotkey #{i}")
            print(f"     SS58:  {ss58}")
            print(f"     Hex:   {hk_hex}")
            print(f"     Power: {power / 1e9:,.4f} τ (Raw: {power})")
        else:
            print(f"\n   → Hotkey #{i}")
            print(f"     Invalid Hex Format: {hk_hex}")

    print("\n" + "="*50)
    print(f"✅ Total Combined Voting Power: {total_power / 1e9:,.4f} τ")
    print("="*50 + "\n")

if __name__ == "__main__":
    main()