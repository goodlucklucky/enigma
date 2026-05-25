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
List recent proposals or view a specific proposal from TreasuryController with payload decoding.
"""

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path


class ProposalViewer:
    STATES = {
        0: "Pending",
        1: "Active",
        2: "Canceled",
        3: "Defeated",
        4: "Succeeded",
        5: "Queued",
        6: "Expired",
        7: "Executed"
    }

    def __init__(self):
        self.contract = None
        self._current_block = None

    def cast_call(self, function_sig: str, args: list = None):
        cmd = ["cast", "call", self.contract, function_sig] + (args or []) + ["--rpc-url", self.rpc]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return None
        return result.stdout.strip()

    def get_state(self, proposal_id: int) -> str:
        res = self.cast_call("state(uint256)(uint8)", [str(proposal_id)])
        if res is not None:
            state_int = int(res.split()[0])
            state_name = self.STATES.get(state_int, f"Unknown")
            return f"{state_int} ({state_name})"
        return "Error"

    def get_current_block(self) -> int:
        if self._current_block is None:
            res = subprocess.run(["cast", "block-number", "--rpc-url", self.rpc], capture_output=True, text=True)
            clean_res = res.stdout.strip().split()[0] if res.returncode == 0 and res.stdout.strip() else ""
            self._current_block = int(clean_res) if clean_res.isdigit() else 0
        return self._current_block

    def get_proposal_timings(self, proposal_id: int):
        snapshot_res = self.cast_call("proposalSnapshot(uint256)(uint256)", [str(proposal_id)])
        deadline_res = self.cast_call("proposalDeadline(uint256)(uint256)", [str(proposal_id)])
        eta_res = self.cast_call("proposalEta(uint256)(uint256)", [str(proposal_id)])

        snapshot = int(snapshot_res.split()[0]) if snapshot_res and snapshot_res.split()[0].isdigit() else 0
        deadline = int(deadline_res.split()[0]) if deadline_res and deadline_res.split()[0].isdigit() else 0
        eta = int(eta_res.split()[0]) if eta_res and eta_res.split()[0].isdigit() else 0

        return snapshot, deadline, eta, self.get_current_block()

    def evm_to_ss58(self, evm_addr: str) -> str:
        script_path = Path(__file__).parent / "evm_to_ss58.py"
        if not script_path.exists():
            return "unknown"
        res = subprocess.run([sys.executable, str(script_path), evm_addr], capture_output=True, text=True)
        if res.returncode == 0:
            return res.stdout.strip().replace("SS58: ", "")
        return "error"

    def get_hotkeys_for_evm(self, evm_addr: str) -> list:
        cmd = ["cast", "call", self.contract, "getHotkeysForAddress(address)(bytes32[])", evm_addr, "--rpc-url", self.rpc]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            output = res.stdout.strip()
            if output and output != "[]":
                clean_output = output.strip("[]").replace('"', '').replace("'", "")
                hex_keys = [k.strip() for k in clean_output.split(",") if k.strip()]

                ss58_keys = []
                try:
                    import substrateinterface
                    for hk in hex_keys:
                        if hk.startswith("0x") and len(hk) == 66:
                            kp = substrateinterface.Keypair(public_key=bytes.fromhex(hk[2:]), ss58_format=42)
                            ss58_keys.append(kp.ss58_address)
                        else:
                            ss58_keys.append(hk)
                except Exception:
                    ss58_keys = hex_keys
                return ss58_keys
        return []

    def decode_payload(self, calldata_str: str, target_addr: str) -> str:
        raw_calldata = calldata_str.strip("[]").split(",")[0].strip()

        if not raw_calldata.startswith("0x") or len(raw_calldata) < 10:
            ss58_target = self.evm_to_ss58(target_addr)
            return f"Type: Native TAO Transfer\n      Recipient: {target_addr} (SS58: {ss58_target})"

        signatures = {
            "updateTrustedValidators(address[],bool[])": ["Validators", "Trusted Status"],
            "transfer(address,uint256)": ["ERC20 Recipient", "Amount"],
            "transferStake(bytes32,bytes32,uint256,uint256,uint256)": [
                "Destination Coldkey", "Source Hotkey", "Origin Netuid",
                "Destination Netuid", "Amount (Alpha)"
            ]
        }

        actual_selector = raw_calldata[:10].lower()

        for sig, labels in signatures.items():
            cmd_sig = ["cast", "sig", sig]
            res_sig = subprocess.run(cmd_sig, capture_output=True, text=True)
            expected_selector = res_sig.stdout.strip().lower()

            if actual_selector == expected_selector:
                cmd = ["cast", "calldata-decode", sig, raw_calldata]
                res = subprocess.run(cmd, capture_output=True, text=True)

                if res.returncode == 0:
                    lines = res.stdout.strip().split('\n')
                    method_name = sig.split('(')[0]

                    output = f"Type: {method_name}\n"

                    if len(lines) == len(labels):
                        for label, val in zip(labels, lines):
                            clean_val = val.strip("[]")
                            raw_str_val = clean_val.split(' ')[0]

                            if label == "Amount (Alpha)":
                                try:
                                    # Alpha transfers use 9 decimals (RAO)
                                    alpha_amt = float(raw_str_val) / 1e9
                                    val = f"{alpha_amt:,.4f} Alpha (Raw: {raw_str_val})"
                                except ValueError:
                                    pass
                            elif label == "Amount":
                                try:
                                    erc20_amt = float(raw_str_val) / 1e18
                                    val = f"{erc20_amt:,.4f} Tokens (Raw: {raw_str_val})"
                                except ValueError:
                                    pass
                            elif "key" in label.lower() and clean_val.startswith("0x") and len(clean_val) == 66:
                                try:
                                    import substrateinterface
                                    kp = substrateinterface.Keypair(public_key=bytes.fromhex(clean_val[2:]), ss58_format=42)
                                    val = f"{val} (SS58: {kp.ss58_address})"
                                except Exception:
                                    pass
                            elif "0x" in clean_val:
                                evm_addrs = re.findall(r'0x[a-fA-F0-9]{40}(?![a-fA-F0-9])', clean_val)
                                if evm_addrs:
                                    val_mod = val
                                    for evm_addr in set(evm_addrs):
                                        ss58_mapped = self.evm_to_ss58(evm_addr)
                                        hotkeys = self.get_hotkeys_for_evm(evm_addr)
                                        hk_info = f" [Hotkeys: {', '.join(hotkeys)}]" if hotkeys else ""

                                        if ss58_mapped and ss58_mapped not in ("unknown", "error"):
                                            replacement = f"{evm_addr} (SS58: {ss58_mapped}){hk_info}"
                                        else:
                                            replacement = f"{evm_addr}{hk_info}"

                                        val_mod = val_mod.replace(evm_addr, replacement)
                                    val = val_mod

                            output += f"      {label}: {val}\n"
                    else:
                        output += f"      Raw Args: {res.stdout.strip().replace(chr(10), ' ')}\n"

                    return output.strip()

        return f"Type: Unknown Payload\n      Selector: {actual_selector}\n      Raw: {raw_calldata[:60]}..."

    def format_time_estimate(self, secs: int) -> str:
        if secs <= 0:
            return "now"
        if secs < 60:
            return f"{secs} secs"
        mins = secs // 60
        if mins < 60:
            return f"{mins} mins"
        hours = mins // 60
        rem_mins = mins % 60
        if hours < 24:
            return f"{hours} hrs {rem_mins} mins"
        days = hours // 24
        rem_hours = hours % 24
        return f"{days} days {rem_hours} hrs"

    def _print_formatted_proposal(self, prop_id: str, targets: str, values: str, calldatas: str, desc_hash: str):
        """Helper to format and print the proposal details consistently."""
        try:
            numeric_id = int(str(prop_id), 0)
            hex_id = hex(numeric_id)
        except ValueError:
            numeric_id = prop_id
            hex_id = prop_id

        state_str = self.get_state(numeric_id)
        state_int = -1
        if state_str != "Error":
            state_int = int(state_str.split()[0])

        snapshot, deadline, eta, current_block = self.get_proposal_timings(numeric_id)

        timing_str = f"Snapshot: {snapshot} | Deadline: {deadline}"
        if state_int == 0: # Pending
            blocks = snapshot - current_block
            est = self.format_time_estimate(blocks * 12)
            timing_str = f"Voting starts in ~{blocks} blocks (~{est}) (at block {snapshot})"
        elif state_int == 1: # Active
            blocks = deadline - current_block
            est = self.format_time_estimate(blocks * 12)
            timing_str = f"Voting ends in ~{blocks} blocks (~{est}) (at block {deadline})"
        elif state_int == 5: # Queued
            now = int(time.time())
            if eta > now:
                est = self.format_time_estimate(eta - now)
                timing_str = f"Timelock expires in ~{est}"
            else:
                timing_str = "Timelock expired - Ready to Execute!"

        clean_target = targets.strip("[]")

        # Format Native TAO Value
        clean_value_str = values.strip("[]").split(' ')[0]
        try:
            native_tao_amt = float(clean_value_str) / 1e18
            formatted_values = f"{native_tao_amt:,.4f} TAO"
        except ValueError:
            formatted_values = values

        decoded_info = self.decode_payload(calldatas, clean_target)

        print(f"    ID:       {hex_id}")
        print(f"    State:    {state_str}")
        print(f"    Timing:   {timing_str}")
        print(f"    Target:   {clean_target}")
        print(f"    Value:    {formatted_values}")
        print(f"    DescHash: {desc_hash}")
        print(f"    Payload:  {decoded_info}")
        print("-" * 80)

    def _print_contract_config(self):
        print("🔍 Contract Configuration:")

        name = self.cast_call("name()(string)")
        name = name.strip('"') if name else "Unknown"

        def extract_int(val_str):
            if val_str:
                parts = val_str.split()
                if parts and parts[0].isdigit():
                    return int(parts[0])
            return 0

        target_netuid = extract_int(self.cast_call("TARGET_NETUID()(uint16)"))
        treasury_admin = self.cast_call("treasuryAdmin()(address)")

        tao_limit = extract_int(self.cast_call("TAO_LIMIT()(uint256)"))
        alpha_limit = extract_int(self.cast_call("ALPHA_LIMIT()(uint256)"))
        erc20_limit = extract_int(self.cast_call("ERC20_LIMIT()(uint256)"))
        limit_reset_period = extract_int(self.cast_call("LIMIT_RESET_PERIOD()(uint256)"))
        success_threshold = extract_int(self.cast_call("SUCCESS_THRESHOLD_BPS()(uint256)"))
        quorum_bps = extract_int(self.cast_call("SUPPORT_THRESHOLD_NUMERATOR()(uint256)"))
        proposal_exp = extract_int(self.cast_call("proposalExpirationBlocks()(uint256)"))
        voting_delay = extract_int(self.cast_call("votingDelay()(uint256)"))
        voting_period = extract_int(self.cast_call("votingPeriod()(uint256)"))
        proposal_threshold = extract_int(self.cast_call("proposalThreshold()(uint256)"))

        # Get Min Delay from Timelock
        timelock_addr = self.cast_call("timelock()(address)")
        min_delay = 0
        if timelock_addr:
            min_delay = extract_int(subprocess.run(["cast", "call", timelock_addr, "getMinDelay()(uint256)", "--rpc-url", self.rpc], capture_output=True, text=True).stdout)

        print(f"  Name:                  {name}")
        print(f"  Target NetUID:         {target_netuid}")
        print(f"  Treasury Admin:        {treasury_admin}")
        print(f"  TAO Limit:             {tao_limit / 1e18:,.4f} TAO")
        print(f"  Alpha Limit:           {alpha_limit / 1e9:,.4f} Alpha")
        print(f"  ERC20 Limit:           {erc20_limit / 1e18:,.4f} Tokens")
        print(f"  Limit Reset Period:    {limit_reset_period} seconds (~{self.format_time_estimate(limit_reset_period)})")
        print(f"  Success Threshold:     {success_threshold} BPS ({success_threshold / 100:.1f}%)")
        print(f"  Quorum:                {quorum_bps} BPS ({quorum_bps / 100:.1f}%)")
        print(f"  Proposal Expiration:   {proposal_exp} blocks (~{self.format_time_estimate(proposal_exp * 12)})")
        print(f"  Voting Delay:          {voting_delay} blocks (~{self.format_time_estimate(voting_delay * 12)})")
        print(f"  Voting Period:         {voting_period} blocks (~{self.format_time_estimate(voting_period * 12)})")
        print(f"  Timelock Delay:        {min_delay} seconds (~{self.format_time_estimate(min_delay)})")
        print("=" * 100)

    def run(self):
        parser = argparse.ArgumentParser(description="View governance proposals")
        parser.add_argument("--rpc", required=True, help="RPC URL")
        parser.add_argument("--contract", required=True, help="TreasuryController address")
        parser.add_argument("--limit", type=int, default=10, help="Number of recent proposals to show (if no ID provided)")
        parser.add_argument("--id", type=str, help="Specific Proposal ID to fetch")
        args = parser.parse_args()

        self.rpc = args.rpc
        self.contract = args.contract

        print(f"\n📋 Fetching from: {self.contract}")
        print("=" * 100)
        self._print_contract_config()

        # ---------------------------------------------------------
        # Branch 1: Fetch a specific proposal by ID
        # ---------------------------------------------------------
        if args.id:
            print(f"→ Inspecting Proposal ID: {args.id}")
            # Note: proposalDetails returns 4 items: (targets, values, calldatas, descriptionHash)
            raw_details = self.cast_call(
                "proposalDetails(uint256)(address[],uint256[],bytes[],bytes32)",
                [args.id]
            )

            if not raw_details:
                print(f"    ❌ Failed to fetch details. Are you sure Proposal '{args.id}' exists?")
                return

            output = raw_details.split('\n')
            if len(output) >= 4:
                targets = output[0].strip()
                values = output[1].strip()
                calldatas = output[2].strip()
                desc_hash = output[3].strip()

                self._print_formatted_proposal(args.id, targets, values, calldatas, desc_hash)
            else:
                print(f"    Raw Output: {raw_details}")

            return

        # ---------------------------------------------------------
        # Branch 2: List recent proposals
        # ---------------------------------------------------------
        res_count = self.cast_call("proposalCount()(uint256)")
        if res_count is None:
            print("❌ Failed to fetch proposal count. Is the contract address correct?")
            return

        total = int(res_count.split()[0])
        print(f"Total proposals ever created: {total}\n")

        if total == 0:
            print("No proposals found.")
            return

        start = max(0, total - args.limit)

        for i in range(total - 1, start - 1, -1):
            print(f"→ Index #{i}")

            # Note: proposalDetailsAt returns 5 items: (id, targets, values, calldatas, descriptionHash)
            raw_details = self.cast_call(
                "proposalDetailsAt(uint256)(uint256,address[],uint256[],bytes[],bytes32)",
                [str(i)]
            )

            if not raw_details:
                print("    ❌ Failed to fetch details.")
                print("-" * 80)
                continue

            output = raw_details.split('\n')
            if len(output) >= 5:
                prop_id = output[0].split()[0].strip()
                targets = output[1].strip()
                values = output[2].strip()
                calldatas = output[3].strip()
                desc_hash = output[4].strip()

                self._print_formatted_proposal(prop_id, targets, values, calldatas, desc_hash)
            else:
                print(f"    Raw Output: {raw_details}")

        print("\n💡 Tip: Use --limit <number> to see more proposals, or --id <proposal_id> to inspect a specific one.")


if __name__ == "__main__":
    ProposalViewer().run()
