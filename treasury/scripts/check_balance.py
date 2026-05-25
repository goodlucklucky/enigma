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
Check Vault / Governor Status
"""

import argparse
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import bittensor as bt


class VaultChecker:
    def __init__(self):
        self.subtensor = None

    def http_to_ws(self, url: str) -> str:
        """Convert HTTP RPC URL to WebSocket URL for bittensor"""
        parsed = urlparse(url)
        if parsed.scheme == "http":
            ws_url = urlunparse(parsed._replace(scheme="ws"))
        elif parsed.scheme == "https":
            ws_url = urlunparse(parsed._replace(scheme="wss"))
        else:
            ws_url = url
        return ws_url

    def get_ss58(self, evm_address: str) -> str:
        """Convert EVM address to SS58 using evm_to_ss58.py"""
        script_path = Path(__file__).parent / "evm_to_ss58.py"
        if not script_path.exists():
            print(f"❌ Error: evm_to_ss58.py not found at {script_path}")
            sys.exit(1)

        result = subprocess.run(
            [sys.executable, str(script_path), evm_address],
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            print(f"❌ Failed to convert EVM to SS58:\n{result.stderr}")
            sys.exit(1)

        output = result.stdout.strip()
        if "SS58:" in output:
            return output.split("SS58: ")[-1].strip()
        return output

    def get_evm_balance(self, evm_address: str, rpc_url: str) -> float:
        """Get EVM balance in TAO"""
        try:
            result = subprocess.run(
                ["cast", "balance", evm_address, "--rpc-url", rpc_url],
                capture_output=True,
                text=True,
                check=True
            )
            wei = int(result.stdout.strip().split()[0])
            return wei / 1e18
        except Exception as e:
            print(f"⚠️ Could not fetch EVM balance: {e}")
            return 0.0

    def get_substrate_balance(self, ss58_address: str) -> float:
        try:
            balance = self.subtensor.get_balance(ss58_address)
            return float(balance)
        except Exception as e:
            print(f"⚠️ Could not fetch Substrate balance: {e}")
            return 0.0

    def scan_all_stakes(self, coldkey_ss58: str):
        """Fetches all stake info for a coldkey across all hotkeys/netuids."""
        try:
            print(f"\nScanning network for all stakes on coldkey: {coldkey_ss58}...")

            # The SDK handles the complex dTAO routing automatically
            stake_info_list = self.subtensor.get_stake_info_for_coldkey(coldkey_ss58)

            if not stake_info_list:
                print("  -> No stake found on any hotkey for this coldkey.")
                return

            print("\nFound Stake:")
            for info in stake_info_list:
                # Safely extract values depending on the SDK version's object structure
                stake_val = float(info.stake) if hasattr(info, 'stake') else 0.0
                if stake_val > 0:
                    hotkey = getattr(info, 'hotkey_ss58', 'Unknown Hotkey')
                    netuid = getattr(info, 'netuid', 'Root/Legacy')
                    print(f"  -> Hotkey : {hotkey}")
                    print(f"     Netuid : {netuid}")
                    print(f"     Amount : {stake_val:,.4f} α/τ\n")

        except Exception as e:
            print(f"⚠️ Could not fetch stake info: {e}")

    def run(self):
        parser = argparse.ArgumentParser(description="Check Vault / Governor balances and stake")
        parser.add_argument("--address", required=True, help="EVM address of Vault or Governor")
        parser.add_argument("--rpc", required=True, help="RPC URL")

        args = parser.parse_args()

        ws_url = self.http_to_ws(args.rpc)
        self.subtensor = bt.Subtensor(network=ws_url)

        print(f"\n{'='*90}")
        print(f"EVM Address : {args.address}")

        ss58 = self.get_ss58(args.address)
        print(f"SS58 Address: {ss58}")

        evm_bal = self.get_evm_balance(args.address, args.rpc)
        sub_bal = self.get_substrate_balance(ss58)

        print(f"\nBalances:")
        print(f"  EVM (TAO)      : {evm_bal:,.4f} τ")
        print(f"  Substrate (TAO): {sub_bal:,.4f} τ")

        # Run the broad scan instead of the specific query
        self.scan_all_stakes(ss58)

        print(f"{'='*90}\n")


if __name__ == "__main__":
    checker = VaultChecker()
    checker.run()
