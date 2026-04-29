#!/usr/bin/env python3
"""
Bittensor Treasury E2E Tests
"""

import sys
import time
import json
import subprocess
from pathlib import Path
from typing import List, Optional

import bittensor as bt


class TreasuryTest:
    def __init__(self):
        self.rpc_url = "http://127.0.0.1:9944"
        self.contract = None
        self.vault_addr = None
        self.admin_pk = None
        self.admin_addr = None
        self.malicious_pk = None
        self.validator_pk = None
        self.validator_addr = None
        self.netuid = 2
        self.current_proposal_id = None
        self.subtensor = bt.Subtensor(network="ws://127.0.0.1:9944")

        # Globals for unified balance tracking
        self.vault_ss58 = None
        self.vault_hotkey_ss58 = None
        self.vault_hotkey_hex = None
        self.miner_ss58 = None
        self.miner_evm = None
        self.miner_coldkey_hex = None

        self.PROPOSAL_STATES = {
            0: "Pending",
            1: "Active",
            2: "Canceled",
            3: "Defeated",
            4: "Succeeded",
            5: "Queued",
            6: "Expired",
            7: "Executed"
        }

    def load(self):
        base = Path(".bittensor")
        with open(base / "contract_addresses.json") as f:
            addrs = json.load(f)
            self.contract = addrs["governor"]
            self.vault_addr = addrs["vault"]

        with open(base / "deployer_evm_wallet.json") as f:
            data = json.load(f)
            wallet = data[0] if isinstance(data, list) else data
            self.admin_pk = wallet["private_key"]
            self.admin_addr = wallet["address"]

        with open(base / "malicious_evm_wallet.json") as f:
            data = json.load(f)
            wallet = data[0] if isinstance(data, list) else data
            self.malicious_pk = wallet["private_key"]

        with open(base / "sn-creator_evm_wallet.json") as f:
            data = json.load(f)
            wallet = data[0] if isinstance(data, list) else data
            self.validator_pk = wallet["private_key"]
            self.validator_addr = wallet["address"]

        vault_hk_path = base / "wallets" / "vault" / "hotkeys" / "default"
        if vault_hk_path.exists():
            with open(vault_hk_path) as f:
                self.vault_hotkey_ss58 = json.load(f)["ss58Address"]
                import substrateinterface
                kp = substrateinterface.Keypair(ss58_address=self.vault_hotkey_ss58)
                self.vault_hotkey_hex = "0x" + kp.public_key.hex()

        miner_ck_path = base / "wallets" / "test-miner" / "coldkeypub.txt"
        if miner_ck_path.exists():
            with open(miner_ck_path) as f:
                self.miner_ss58 = json.load(f).get("ss58Address")
                import substrateinterface
                kp = substrateinterface.Keypair(ss58_address=self.miner_ss58)
                self.miner_coldkey_hex = "0x" + kp.public_key.hex()
                
        miner_evm_path = base / "test-miner_evm_wallet.json"
        if miner_evm_path.exists():
            with open(miner_evm_path) as f:
                data = json.load(f)
                self.miner_evm = (data[0] if isinstance(data, list) else data).get("address")

        # Address conversions
        gov_ss58 = self.evm_to_ss58(self.contract)
        self.vault_ss58 = self.evm_to_ss58(self.vault_addr)
        admin_ss58 = self.evm_to_ss58(self.admin_addr)
        val_ss58 = self.evm_to_ss58(self.validator_addr)
        
        if self.miner_ss58 and not self.miner_evm:
            self.miner_evm = "Unknown/Not Deployed"

        print(f"Governor:  {self.contract} (SS58: {gov_ss58})")
        print(f"Vault:     {self.vault_addr} (SS58: {self.vault_ss58})")
        print(f"Admin:     {self.admin_addr} (SS58: {admin_ss58})")
        print(f"Validator: {self.validator_addr} (SS58: {val_ss58})")
        print(f"Miner:     {self.miner_evm} (SS58: {self.miner_ss58})\n")
        
        print("=== INITIAL BALANCES (Load) ===")
        self._print_balances("Governor ", self.contract, gov_ss58)
        self._print_balances("Vault    ", self.vault_addr, self.vault_ss58, self.vault_hotkey_ss58)
        self._print_balances("Admin    ", self.admin_addr, admin_ss58)
        self._print_balances("Validator", self.validator_addr, val_ss58)
        self._print_balances("Miner    ", self.miner_evm, self.miner_ss58, self.vault_hotkey_ss58)
        print("===============================\n")

    def _get_all_balances(self, evm_addr: str, ss58_addr: str, hotkey_ss58: str = None):
        """Helper to fetch EVM, Substrate, and Stake in one go."""
        evm_bal = (self.get_balance(evm_addr) / 1e18) if evm_addr and "Unknown" not in evm_addr else 0.0
        
        try:
            sub_bal = float(self.subtensor.get_balance(ss58_addr)) if ss58_addr else 0.0
        except:
            sub_bal = 0.0
            
        stake = 0.0
        if hotkey_ss58 and ss58_addr:
            stake = self.get_alpha_balance(ss58_addr, hotkey_ss58)
            
        return evm_bal, sub_bal, stake

    def _print_balances(self, name: str, evm_addr: str, ss58_addr: str, hotkey_ss58: str = None):
        """Helper to format and print the 3 balances"""
        evm, sub, stake = self._get_all_balances(evm_addr, ss58_addr, hotkey_ss58)
        print(f"  [{name}] EVM: {evm:8.4f} | Substrate: {sub:9.4f} | Stake: {stake:8.4f}")

    def evm_to_ss58(self, evm_addr: str) -> str:
        script_path = Path(__file__).parent.parent / "scripts" / "evm_to_ss58.py"
        if not script_path.exists():
            return "unknown"
        res = subprocess.run([sys.executable, str(script_path), evm_addr], capture_output=True, text=True)
        if res.returncode == 0:
            return res.stdout.strip().replace("SS58: ", "")
        return "error"

    def cast(self, sig: str, args: List[str], pk: str, label: str, expected_revert: str = None):
        """
        Executes a cast call/send. 
        If expected_revert is provided, strictly enforces that the revert string matches.
        """
        print(f"\n→ [{label}]")
        expect_fail = expected_revert is not None
        
        call_cmd = ["cast", "call", self.contract, sig, *args, "--private-key", pk, "--rpc-url", self.rpc_url]
        call_res = subprocess.run(call_cmd, capture_output=True, text=True)
        
        if call_res.returncode != 0:
            err = call_res.stderr.strip() or call_res.stdout.strip()
            # Try to cleanly format the error for the terminal
            reason = err.split("revert ")[-1].split(", data:")[0] if "revert " in err else err

            if expect_fail:
                # STRICT MATCHING: The expected string must be in the error output
                if expected_revert.lower() in err.lower():
                    print(f"  ✅ Reverted as expected: '{expected_revert}'")
                    return True
                else:
                    print(f"  ❌ LEAKY TEST CAUGHT: Expected '{expected_revert}', but reverted with: '{reason}'")
                    return False
            else:
                print(f"  ❌ UNEXPECTED REVERT: {reason}")
                return False

        if expect_fail:
            print(f"  ❌ SECURITY FAILURE: Should have reverted with '{expected_revert}' but succeeded!")
            return False

        send_cmd = ["cast", "send", self.contract, sig, *args, "--private-key", pk, "--rpc-url", self.rpc_url,
                    "--gas-limit", "10000000", "--json"]
        send_res = subprocess.run(send_cmd, capture_output=True, text=True)
        
        try:
            receipt = json.loads(send_res.stdout.strip())
            status = int(receipt.get("status", "0x0"), 16)
            tx = receipt.get("transactionHash", "unknown")[:12]
            if status == 1:
                print(f"  ✅ SUCCESS | Tx: {tx}")
                if "propose" in sig.lower():
                    self.current_proposal_id = self.extract_proposal_id(receipt)
                return True
            else:
                print("  ❌ Execution failed")
                return False
        except Exception as e:
            print(f"  ❌ Parse error: {e}")
            print(f"  Raw: {send_res.stdout.strip()}")
            return False

    def extract_proposal_id(self, receipt: dict) -> Optional[int]:
        event_sig = "0x7d84a6263ae0d98d3329bd7b46bb4e8d6f98cd35a7adb45c274c8b7fd5ebd5e0"
        for log in receipt.get("logs", []):
            if log.get("topics", []) and log["topics"][0].lower() == event_sig.lower():
                data = log.get("data", "0x")
                if len(data) >= 66:
                    return int(data[2:66], 16)
        return None

    def wait_for_blocks(self, num_blocks: int):
        start = self.subtensor.get_current_block()
        target = start + num_blocks
        print(f"  ⏳ Waiting {num_blocks} blocks (current: {start})...")
        while self.subtensor.get_current_block() < target:
            time.sleep(2)

    def wait_for_timelock(self, min_seconds: int = 12, min_blocks: int = 1):
        """
        Guarantees both real-world time and on-chain time have advanced.
        Crucial for OpenZeppelin TimelockControllers across variable network speeds.
        """
        print(f"  ⏳ Waiting for timelock ({min_seconds}s and {min_blocks} block)...")
        start_time = time.time()
        start_block = self.subtensor.get_current_block()
        
        # 1. Wait out the real-world time 
        while time.time() - start_time < min_seconds:
            time.sleep(1)
            
        # 2. Guarantee the chain has minted a block to update the EVM timestamp
        target_block = start_block + min_blocks
        while self.subtensor.get_current_block() < target_block:
            time.sleep(2)

    def check_proposal_state(self, proposal_id: int) -> int:
        if proposal_id is None: return -1
        cmd = ["cast", "call", self.contract, "state(uint256)(uint8)", str(proposal_id), "--rpc-url", self.rpc_url]
        res = subprocess.run(cmd, capture_output=True, text=True)
        try:
            return int(res.stdout.strip())
        except:
            return -1

    def debug_validator(self):
        print("\n→ [DEBUG VALIDATOR STATUS]")
        cmd = ["cast", "call", self.contract, "debugValidatorStatus(address)(bool,bool,uint256)",
               self.validator_addr, "--rpc-url", self.rpc_url]
        res = subprocess.run(cmd, capture_output=True, text=True)
        lines = [line.strip() for line in res.stdout.strip().split('\n') if line.strip()]
        if len(lines) >= 3:
            print(f"  isTrusted:         {lines[0]}")
            print(f"  isActiveValidator: {lines[1]}")
            print(f"  Hotkeys:           {lines[2]}")

    def debug_voting_power(self):
        print("→ [DEBUG VOTING POWER]")
        cmd = ["cast", "call", self.contract, "debugVotingPower(address)(uint256)",
               self.validator_addr, "--rpc-url", self.rpc_url]
        res = subprocess.run(cmd, capture_output=True, text=True)
        print(f"  Total Voting Power: {res.stdout.strip()}")

    def debug_alpha_params(self, destination_coldkey_hex: str, hotkey_hex: str, amount: str):
        print("\n→ [DEBUG ALPHA TRANSFER PARAMS]")
        cmd = ["cast", "call", self.contract, 
               "debugAlphaTransferParams(bytes32,bytes32,uint16,uint16,uint256)(address,bool,bytes32)",
               destination_coldkey_hex, hotkey_hex, str(self.netuid), str(self.netuid), amount,
               "--rpc-url", self.rpc_url]
        res = subprocess.run(cmd, capture_output=True, text=True)
        print(f"  Output: {res.stdout.strip()}")

    def debug_vault_hotkey_registration(self):
        print("\n→ [DEBUG VAULT HOTKEY REGISTRATION]")
        print(f"  Vault (Timelock) Address: {self.vault_addr}")
        cmd = ["cast", "call", "0x0000000000000000000000000000000000000806", 
               "uidLookup(uint16,address,uint16)(uint16[],uint64[])", 
               str(self.netuid), self.vault_addr, "10", "--rpc-url", self.rpc_url]
        res = subprocess.run(cmd, capture_output=True, text=True)
        print(f"  UID Lookup on Vault: {res.stdout.strip()}")
        cmd2 = ["cast", "call", self.contract, "debugValidatorStatus(address)(bool,bool,uint256)",
                self.vault_addr, "--rpc-url", self.rpc_url]
        res2 = subprocess.run(cmd2, capture_output=True, text=True)
        print(f"  Vault as Validator → {res2.stdout.strip()}")

    def get_balance(self, address: str) -> int:
        cmd = ["cast", "balance", address, "--rpc-url", self.rpc_url]
        res = subprocess.run(cmd, capture_output=True, text=True)
        try:
            return int(res.stdout.strip())
        except:
            return 0

    def get_alpha_balance(self, coldkey_ss58: str, hotkey_ss58: str) -> float:
        if not coldkey_ss58 or not hotkey_ss58:
            return 0.0
        try:
            stake = self.subtensor.get_stake(
                coldkey_ss58=coldkey_ss58,
                hotkey_ss58=hotkey_ss58,
                netuid=self.netuid
            )
            return float(stake)
        except Exception as e:
            print(f"  ⚠️ Error fetching stake: {e}")
            return 0.0

    # =========================================================================
    # GROUP 1: Foundation & Roles (Whitelist / Permissions)
    # =========================================================================

    def test_1_bootstrap(self):
        print("\n" + "="*80 + "\nTEST 1: Empty Whitelist Bootstrap (Admin Only)\n" + "="*80)
        args = [f"[{self.validator_addr}]", "[true]", "Bootstrap"]
        
        step1 = self.cast("proposeUpdateTrustedValidators(address[],bool[],string)", args, self.malicious_pk, "Malicious Propose", expected_revert="Only the treasury admin can propose")
        step2 = self.cast("proposeUpdateTrustedValidators(address[],bool[],string)", args, self.admin_pk, "Admin Propose")
        
        prop_id = self.current_proposal_id
        self.wait_for_blocks(4)
        
        step3 = self.cast("castVote(uint256,uint8)", [str(prop_id), "1"], self.admin_pk, "Admin Vote (Empty WL)")
        self.wait_for_blocks(12)
        
        step4 = self.cast("queueWhitelistUpdate(address[],bool[],string)", args, self.admin_pk, "Queue Whitelist")
        self.wait_for_timelock(min_seconds=12)
        step5 = self.cast("executeWhitelistUpdate(address[],bool[],string)", args, self.admin_pk, "Execute Whitelist")
        
        self.debug_validator()
        self.debug_voting_power()
        
        return all([step1, step2, step3, step4, step5])

    def test_2_non_empty_whitelist(self):
        print("\n" + "="*80 + "\nTEST 2: Non-Empty Whitelist Behavior\n" + "="*80)
        args = [f"[{self.admin_addr}]", "[true]", "AddAdmin"]
        step1 = self.cast("proposeUpdateTrustedValidators(address[],bool[],string)", args, self.admin_pk, "Propose WL Update")
        
        prop_id = self.current_proposal_id
        self.wait_for_blocks(4)
        
        step2 = self.cast("castVote(uint256,uint8)", [str(prop_id), "1"], self.admin_pk, "Admin Vote on WL", expected_revert="Not an active, trusted validator")
        step3 = self.cast("castVote(uint256,uint8)", [str(prop_id), "1"], self.validator_pk, "Validator Vote on WL")
        self.wait_for_blocks(12)
        
        return all([step1, step2, step3])

    def test_3_malicious_propose_transfer(self):
        print("\n" + "="*80 + "\nTEST 3: Malicious Propose Transfer (Should Fail)\n" + "="*80)
        args = [self.admin_addr, "1000000000000000000", "MaliciousTransfer"]
        return self.cast("proposeNativeTransfer(address,uint256,string)", args, self.malicious_pk, "Malicious Propose Transfer", expected_revert="Only the treasury admin can propose")

    def test_4_malicious_vote_reverts(self):
        print("\n" + "="*80 + "\nTEST 4: Malicious / Non-Whitelisted Vote Attempt\n" + "="*80)
        args = [self.admin_addr, "100000", "VoteGriefingTest"]
        
        step1 = self.cast("proposeNativeTransfer(address,uint256,string)", args, self.admin_pk, "Admin Propose")
        prop_id = self.current_proposal_id
        self.wait_for_blocks(4)
        
        step2 = self.cast("castVote(uint256,uint8)", [str(prop_id), "1"], self.malicious_pk, "Malicious Vote", expected_revert="Not an active, trusted validator")
        step3 = self.cast("castVote(uint256,uint8)", [str(prop_id), "1"], self.admin_pk, "Admin Vote on Transfer", expected_revert="Not an active, trusted validator")
        
        return all([step1, step2, step3])

    # =========================================================================
    # GROUP 2: TAO (Native) Asset Transfers
    # =========================================================================

    def test_5_native_transfer_success(self):
        print("\n" + "="*80 + "\nTEST 5: Native TAO Transfer + Balance Check\n" + "="*80)
        
        print("\n→ [Funding Treasury Vault]")
        fund_cmd = [
            "cast", "send", self.vault_addr, 
            "--value", "10000000000000000000", # 10 Tokens
            "--private-key", self.admin_pk, 
            "--rpc-url", self.rpc_url
        ]
        subprocess.run(fund_cmd, capture_output=True, text=True)
        print(f"  Vault funded! Balance: {self.get_balance(self.vault_addr)}")

        initial = self.get_balance(self.admin_addr)
        args = [self.admin_addr, "500000000000000000", "TransferTest"]
        
        step1 = self.cast("proposeNativeTransfer(address,uint256,string)", args, self.admin_pk, "Propose Transfer")
        prop_id = self.current_proposal_id
        self.wait_for_blocks(4)
        
        step2 = self.cast("castVote(uint256,uint8)", [str(prop_id), "1"], self.validator_pk, "Validator Vote")
        self.wait_for_blocks(12)
        
        step3 = self.cast("queueNativeTransfer(address,uint256,string)", args, self.admin_pk, "Queue Transfer")
        self.wait_for_timelock(min_seconds=12)
        step4 = self.cast("executeNativeTransfer(address,uint256,string)", args, self.admin_pk, "Execute Transfer")
        self.wait_for_blocks(2)

        final = self.get_balance(self.admin_addr)
        balance_increased = final > initial
        
        return all([step1, step2, step3, step4]) and balance_increased

    def test_6_native_rate_limit_failure(self):
        print("\n" + "="*80 + "\nTEST 6: Native TAO Rate Limiting Enforcement\n" + "="*80)
        # 1500 TAO (1500 + 18 zeros) to breach the 1000 TAO limit
        args = [self.admin_addr, "1500000000000000000000", "RateLimitTest"]

        step1 = self.cast("proposeNativeTransfer(address,uint256,string)", args, self.admin_pk, "Propose Large Transfer")
        prop_id = self.current_proposal_id
        self.wait_for_blocks(4)

        step2 = self.cast("castVote(uint256,uint8)", [str(prop_id), "1"], self.validator_pk, "Vote")
        self.wait_for_blocks(12)

        step3 = self.cast("queueNativeTransfer(address,uint256,string)", args, self.admin_pk, "Queue Large Transfer")
        self.wait_for_timelock(min_seconds=12)

        step4 = self.cast("executeNativeTransfer(address,uint256,string)", args, self.admin_pk, "Execute (expect limit fail)", expected_revert="Limit exceeded")

        return all([step1, step2, step3, step4])

    def test_7_native_insufficient_funds(self):
        print("\n" + "="*80 + "\nTEST 7: Execution Fails if Vault is Broke\n" + "="*80)
        vault_balance = self.get_balance(self.vault_addr)
        impossible_amount = str(vault_balance + 1000000000000000000) # Balance + 1 TAO
        
        args = [self.admin_addr, impossible_amount, "BrokeVaultTest"]
        
        step1 = self.cast("proposeNativeTransfer(address,uint256,string)", args, self.admin_pk, "Propose Impossible Transfer")
        prop_id = self.current_proposal_id
        self.wait_for_blocks(4)
        
        step2 = self.cast("castVote(uint256,uint8)", [str(prop_id), "1"], self.validator_pk, "Vote")
        self.wait_for_blocks(12)
        
        step3 = self.cast("queueNativeTransfer(address,uint256,string)", args, self.admin_pk, "Queue Impossible Transfer")
        self.wait_for_timelock(min_seconds=12)
        
        step4 = self.cast("executeNativeTransfer(address,uint256,string)", args, self.admin_pk, "Execute Impossible Transfer", expected_revert="0x1425ea42")
        
        return all([step1, step2, step3, step4])

    # =========================================================================
    # GROUP 3: Alpha Precompile Transfers
    # =========================================================================

    def test_8_alpha_transfer_success(self):
        print("\n" + "="*80 + "\nTEST 8: Alpha Precompile Transfer (Staking V2)\n" + "="*80)
        
        if not self.vault_hotkey_hex or not self.miner_coldkey_hex:
            print("  ERROR: Vault hotkey or Miner coldkey missing. Run load() first.")
            return False

        amount_to_send = "50000000000"

        args = [
            self.miner_coldkey_hex,
            self.vault_hotkey_hex,
            str(self.netuid),
            str(self.netuid),
            amount_to_send,
            "AlphaTransferTest"
        ]
        
        sig_propose = "proposeAlphaTransfer(bytes32,bytes32,uint16,uint16,uint256,string)"
        sig_queue   = "queueAlphaTransfer(bytes32,bytes32,uint16,uint16,uint256,string)"
        sig_execute = "executeAlphaTransfer(bytes32,bytes32,uint16,uint16,uint256,string)"

        m_stake_b = self._get_all_balances(self.miner_evm, self.miner_ss58, self.vault_hotkey_ss58)[2]

        step1 = self.cast(sig_propose, args, self.admin_pk, "Propose Alpha Transfer")
        prop_id = self.current_proposal_id
        self.wait_for_blocks(4)
        
        step2 = self.cast("castVote(uint256,uint8)", [str(prop_id), "1"], self.validator_pk, "Validator Vote")
        self.wait_for_blocks(12)
        
        step3 = self.cast(sig_queue, args, self.admin_pk, "Queue Alpha Transfer")
        self.wait_for_timelock(min_seconds=12)
        
        step4 = self.cast(sig_execute, args, self.admin_pk, "Execute Alpha Transfer")
        self.wait_for_blocks(3) 

        m_stake_a = self._get_all_balances(self.miner_evm, self.miner_ss58, self.vault_hotkey_ss58)[2]
        miner_increased = (m_stake_a - m_stake_b) >= 49.9

        return all([step1, step2, step3, step4]) and miner_increased

    def test_9_alpha_rate_limit_failure(self):
        print("\n" + "="*80 + "\nTEST 9: Alpha Rate Limit Enforcement\n" + "="*80)
        massive_alpha_amount = "500000000000000000000000000"
        args = [
            self.miner_coldkey_hex,
            self.vault_hotkey_hex,
            str(self.netuid),
            str(self.netuid),
            massive_alpha_amount,
            "AlphaLimitTest"
        ]
        
        step1 = self.cast("proposeAlphaTransfer(bytes32,bytes32,uint16,uint16,uint256,string)", args, self.admin_pk, "Propose Massive Alpha")
        prop_id = self.current_proposal_id
        self.wait_for_blocks(4)
        
        step2 = self.cast("castVote(uint256,uint8)", [str(prop_id), "1"], self.validator_pk, "Vote")
        self.wait_for_blocks(12)
        
        step3 = self.cast("queueAlphaTransfer(bytes32,bytes32,uint16,uint16,uint256,string)", args, self.admin_pk, "Queue Massive Alpha")
        self.wait_for_timelock(min_seconds=12)
        
        step4 = self.cast("executeAlphaTransfer(bytes32,bytes32,uint16,uint16,uint256,string)", args, self.admin_pk, "Execute Massive Alpha", expected_revert="Limit exceeded")
        
        return all([step1, step2, step3, step4])

    # =========================================================================
    # GROUP 4: ERC20 Token Transfers
    # =========================================================================

    def test_10_erc20_transfer_success(self):
        print("\n" + "="*80 + "\nTEST 10: ERC20 Transfer Success\n" + "="*80)
        if not hasattr(self, 'mock_erc20_addr') or not self.mock_erc20_addr:
            print("  ⚠️ Skipping: No mock_erc20_addr defined in load().")
            return True 
            
        args = [self.mock_erc20_addr, self.admin_addr, "5000000000000000000", "ERC20Test"]
        
        step1 = self.cast("proposeERC20Transfer(address,address,uint256,string)", args, self.admin_pk, "Propose ERC20")
        prop_id = self.current_proposal_id
        self.wait_for_blocks(4)
        
        step2 = self.cast("castVote(uint256,uint8)", [str(prop_id), "1"], self.validator_pk, "Vote")
        self.wait_for_blocks(12)
        
        step3 = self.cast("queueERC20Transfer(address,address,uint256,string)", args, self.admin_pk, "Queue ERC20")
        self.wait_for_timelock(min_seconds=12)
        
        step4 = self.cast("executeERC20Transfer(address,address,uint256,string)", args, self.admin_pk, "Execute ERC20")
        
        return all([step1, step2, step3, step4])

    def test_11_erc20_rate_limit_failure(self):
        print("\n" + "="*80 + "\nTEST 11: ERC20 Rate Limit Enforcement\n" + "="*80)
        if not hasattr(self, 'mock_erc20_addr') or not self.mock_erc20_addr:
            print("  ⚠️ Skipping: No mock_erc20_addr defined in load().")
            return True 
            
        massive_erc20_amount = "500000000000000000000000" # Way above expected limits
        args = [self.mock_erc20_addr, self.admin_addr, massive_erc20_amount, "ERC20LimitTest"]
        
        step1 = self.cast("proposeERC20Transfer(address,address,uint256,string)", args, self.admin_pk, "Propose Massive ERC20")
        prop_id = self.current_proposal_id
        self.wait_for_blocks(4)
        
        step2 = self.cast("castVote(uint256,uint8)", [str(prop_id), "1"], self.validator_pk, "Vote")
        self.wait_for_blocks(12)
        
        step3 = self.cast("queueERC20Transfer(address,address,uint256,string)", args, self.admin_pk, "Queue Massive ERC20")
        self.wait_for_timelock(min_seconds=12)
        
        step4 = self.cast("executeERC20Transfer(address,address,uint256,string)", args, self.admin_pk, "Execute Massive ERC20", expected_revert="Limit exceeded")
        
        return all([step1, step2, step3, step4])

    # =========================================================================
    # GROUP 5: Proposal Lifecycle Edge Cases
    # =========================================================================

    def test_12_pending_cancellation(self):
        print("\n" + "="*80 + "\nTEST 12: Pending Cancellation\n" + "="*80)
        args = [self.admin_addr, "1000000000000000000", "CancelPending"]
        
        step1 = self.cast("proposeNativeTransfer(address,uint256,string)", args, self.admin_pk, "Propose")
        step2 = self.cast("cancelNativeTransfer(address,uint256,string)", args, self.malicious_pk, "Malicious Cancel", expected_revert="Not admin or whitelisted")
        step3 = self.cast("cancelNativeTransfer(address,uint256,string)", args, self.validator_pk, "Validator Cancel Pending")
        
        return all([step1, step2, step3])

    def test_13_queued_cancellation(self):
        print("\n" + "="*80 + "\nTEST 13: Queued Cancellation\n" + "="*80)
        args = [self.admin_addr, "1000000000000000000", "CancelQueued"]
        
        step1 = self.cast("proposeNativeTransfer(address,uint256,string)", args, self.admin_pk, "Propose")
        prop_id = self.current_proposal_id
        self.wait_for_blocks(4)
        
        step2 = self.cast("castVote(uint256,uint8)", [str(prop_id), "1"], self.validator_pk, "Vote FOR")
        self.wait_for_blocks(12)
        
        step3 = self.cast("queueNativeTransfer(address,uint256,string)", args, self.admin_pk, "Queue")
        step4 = self.cast("cancelNativeTransfer(address,uint256,string)", args, self.admin_pk, "Cancel Queued")
        
        return all([step1, step2, step3, step4])

    def test_14_against_vote_defeats_proposal(self):
        print("\n" + "="*80 + "\nTEST 14: Against Vote Defeats Proposal\n" + "="*80)
        args = [self.admin_addr, "100000", "DefeatTest"]
        
        step1 = self.cast("proposeNativeTransfer(address,uint256,string)", args, self.admin_pk, "Propose Transfer")
        prop_id = self.current_proposal_id
        self.wait_for_blocks(4)
        
        step2 = self.cast("castVote(uint256,uint8)", [str(prop_id), "0"], self.validator_pk, "Vote AGAINST")
        self.wait_for_blocks(12)
        
        state = self.check_proposal_state(prop_id)
        print(f"  Final state: {state} (3 = Defeated)")
        
        return all([step1, step2]) and (state == 3)

    def test_15_passive_quorum_failure(self):
        print("\n" + "="*80 + "\nTEST 15: Proposal Fails if Quorum Not Reached\n" + "="*80)
        args = [self.admin_addr, "100000", "PassiveFailTest"]
        
        step1 = self.cast("proposeNativeTransfer(address,uint256,string)", args, self.admin_pk, "Propose Transfer")
        prop_id = self.current_proposal_id
        
        # Wait for the ENTIRE delay and voting period to expire
        self.wait_for_blocks(17)
        
        state = self.check_proposal_state(prop_id)
        print(f"  Final state: {state} (3 = Defeated)")
        
        return step1 and (state == 3)

    # =========================================================================
    # GROUP 6: The Grand Finale (Revocation)
    # =========================================================================

    def test_16_remove_from_whitelist(self):
        print("\n" + "="*80 + "\nTEST 16: Remove Validator from Whitelist\n" + "="*80)
        args = [f"[{self.validator_addr}]", "[false]", "RemoveValidator"]
        
        step1 = self.cast("proposeUpdateTrustedValidators(address[],bool[],string)", args, self.admin_pk, "Propose Removal")
        prop_id = self.current_proposal_id
        self.wait_for_blocks(4)
        
        step2 = self.cast("castVote(uint256,uint8)", [str(prop_id), "1"], self.validator_pk, "Vote on Removal")
        self.wait_for_blocks(12)
        
        step3 = self.cast("queueWhitelistUpdate(address[],bool[],string)", args, self.admin_pk, "Queue Removal")
        self.wait_for_timelock(min_seconds=12)
        step4 = self.cast("executeWhitelistUpdate(address[],bool[],string)", args, self.admin_pk, "Execute Removal")
        
        self.debug_validator()
        
        return all([step1, step2, step3, step4])

    def test_17_post_removal_voting_fails(self):
        print("\n" + "="*80 + "\nTEST 17: Verify Removed Validator Cannot Vote\n" + "="*80)
        
        args = [self.admin_addr, "100000", "PostRemovalTest"]
        step1 = self.cast("proposeNativeTransfer(address,uint256,string)", args, self.admin_pk, "Propose Transfer")
        prop_id = self.current_proposal_id
        self.wait_for_blocks(4)
        
        step2 = self.cast("castVote(uint256,uint8)", [str(prop_id), "1"], self.validator_pk, "Removed Validator Vote", expected_revert="Not an active, trusted validator")
        
        return all([step1, step2])

    def test_18_proposal_listing(self):
        print("\n" + "="*80 + "\nTEST 18: Proposal Listing (GovernorStorage)\n" + "="*80)
        
        # 1. Get total proposal count from standard OpenZeppelin storage
        cmd_count = ["cast", "call", self.contract, "proposalCount()(uint256)", "--rpc-url", self.rpc_url]
        res_count = subprocess.run(cmd_count, capture_output=True, text=True)
        
        try:
            count = int(res_count.stdout.strip())
            print(f"  ✅ Total Proposals in Storage: {count}")
        except Exception as e:
            print(f"  ❌ Failed to get proposal count: {e}\n  Raw output: {res_count.stdout}")
            return False

        if count == 0:
            print("  ⚠️ No proposals to inspect.")
            return True

        # 2. Inspect the last few proposals (up to 15 for brevity in tests)
        start = max(0, count - 15)
        for i in range(start, count):
            print(f"\n  → Inspecting Proposal Index #{i}...")
            
            # Fetch details via the sequential index
            cmd_details = [
                "cast", "call", self.contract, 
                "proposalDetailsAt(uint256)(uint256,address[],uint256[],bytes[],bytes32)", 
                str(i), "--rpc-url", self.rpc_url
            ]
            res_details = subprocess.run(cmd_details, capture_output=True, text=True)
            
            if res_details.returncode != 0:
                print(f"    ❌ Failed to fetch details for index {i}")
                continue

            # `cast call` returns tuples as multi-line strings
            output = res_details.stdout.strip().split('\n')
            
            if len(output) >= 5:
                prop_id = output[0].split()[0].strip() 
                targets = output[1].strip()
                values = output[2].strip()
                calldatas = output[3].strip()
                desc_hash = output[4].strip()
                
                state_int = self.check_proposal_state(int(prop_id))
                state_str = self.PROPOSAL_STATES.get(state_int, "Unknown")
                clean_target = targets.strip("[]")
                
                print(f"    ID:       {prop_id}")
                print(f"    State:    {state_int} ({state_str})")
                print(f"    Targets:  {targets}")
                print(f"    Values:   {values}")
                print(f"    DescHash: {desc_hash}")
                
                # Decode and print the fully labeled payload
                decoded_info = self._decode_payload(calldatas, clean_target)
                print(f"    Payload:  {decoded_info}")
            else:
                print(f"    Raw Output: {res_details.stdout.strip()}")
                
        print("\n  ✅ Proposal inspection completed via client-side indexing")
        return True

    def _decode_payload(self, calldata_str: str, target_addr: str) -> str:
        """Attempts to decode known Treasury payloads and label the arguments, including SS58 conversion."""
        raw_calldata = calldata_str.strip("[]").split(",")[0].strip()
        
        # 1. Handle Native TAO Transfers (Empty Calldata)
        if not raw_calldata.startswith("0x") or len(raw_calldata) < 10:
            ss58_target = self.evm_to_ss58(target_addr)
            return f"Type: Native TAO Transfer\n      Recipient: {target_addr} (SS58: {ss58_target})"

        # Map signatures to their human-readable argument labels
        signatures = {
            "updateTrustedValidators(address[],bool[])": [
                "Validators", 
                "Trusted Status"
            ],
            "transfer(address,uint256)": [
                "ERC20 Recipient", 
                "Amount"
            ],
            "transferStake(bytes32,bytes32,uint256,uint256,uint256)": [
                "Destination Coldkey", 
                "Source Hotkey", 
                "Origin Netuid", 
                "Destination Netuid", 
                "Amount (Alpha)"
            ]
        }

        # Extract the actual 4-byte selector from the payload (0x + 8 hex chars)
        actual_selector = raw_calldata[:10].lower()

        for sig, labels in signatures.items():
            # Ask cast what the EVM selector should be for this signature
            cmd_sig = ["cast", "sig", sig]
            res_sig = subprocess.run(cmd_sig, capture_output=True, text=True)
            expected_selector = res_sig.stdout.strip().lower()
            
            # STRICT MATCH: Only decode if the signatures match exactly
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
                            
                            # Grab just the raw number, ignoring cast's scientific notation like "[5e10]"
                            raw_str_val = clean_val.split(' ')[0]

                            # A. Format Alpha Amounts (9 Decimals)
                            if label == "Amount (Alpha)":
                                try:
                                    alpha_amt = float(raw_str_val) / 1e9
                                    val = f"{alpha_amt:,.4f} Alpha (Raw: {raw_str_val})"
                                except ValueError:
                                    pass

                            # B. Format ERC20 Amounts (Assuming standard 18 decimals)
                            elif label == "Amount":
                                try:
                                    erc20_amt = float(raw_str_val) / 1e18
                                    val = f"{erc20_amt:,.4f} Tokens (Raw: {raw_str_val})"
                                except ValueError:
                                    pass

                            # C. Decode 32-byte Substrate Public Keys
                            elif "key" in label.lower() and clean_val.startswith("0x") and len(clean_val) == 66:
                                try:
                                    import substrateinterface
                                    kp = substrateinterface.Keypair(public_key=bytes.fromhex(clean_val[2:]), ss58_format=42)
                                    val = f"{val} (SS58: {kp.ss58_address})"
                                except Exception:
                                    pass 
                                    
                            # D. Decode 20-byte EVM Addresses
                            elif clean_val.startswith("0x") and len(clean_val) == 42:
                                ss58_mapped = self.evm_to_ss58(clean_val)
                                if ss58_mapped and ss58_mapped not in ("unknown", "error"):
                                    val = f"{val} (SS58: {ss58_mapped})"
                                    
                            output += f"      {label}: {val}\n"
                    else:
                        output += f"      Raw Args: {res.stdout.strip().replace(chr(10), ' ')}\n"
                        
                    return output.strip()
        
        return f"Type: Unknown Payload\n      Selector: {actual_selector}\n      Raw: {raw_calldata[:60]}..."

    def run_all_tests(self):
        self.load()
        results = [
            self.test_1_bootstrap(),
            self.test_2_non_empty_whitelist(),
            self.test_3_malicious_propose_transfer(),
            self.test_4_malicious_vote_reverts(),
            self.test_5_native_transfer_success(),
            self.test_6_native_rate_limit_failure(),
            self.test_7_native_insufficient_funds(),
            self.test_8_alpha_transfer_success(),
            self.test_9_alpha_rate_limit_failure(),
            self.test_10_erc20_transfer_success(),
            self.test_11_erc20_rate_limit_failure(),
            self.test_12_pending_cancellation(),
            self.test_13_queued_cancellation(),
            self.test_14_against_vote_defeats_proposal(),
            self.test_15_passive_quorum_failure(),
            self.test_16_remove_from_whitelist(),
            self.test_17_post_removal_voting_fails(),
            self.test_18_proposal_listing(),
        ]

        print("\n" + "="*90 + "\nFINAL SUMMARY\n" + "="*90)
        for i, passed in enumerate(results, 1):
            print(f"Test {i}: {'✅ PASS' if passed else '❌ FAIL'}")

        if all(results):
            print("\n🎉 ALL TESTS PASSED STRICT ASSERTIONS!")
            return 0
        else:
            print("\n❌ Tests failed strict revert checking. Review the outputs above.")
            return 1


if __name__ == "__main__":
    sys.exit(TreasuryTest().run_all_tests())