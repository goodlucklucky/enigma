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
Production Deployment Script for Treasury Governor & Vault
Calculates realistic mainnet parameters and executes the Foundry deployment.
"""

import os
import sys
import argparse
import subprocess
from pathlib import Path

def to_wei(amount: float) -> str:
    """Converts human-readable token amount to Wei string (10^18)"""
    return str(int(amount * 10**18))

def main():
    parser = argparse.ArgumentParser(description="Deploy Treasury Contracts to Mainnet")
    parser.add_argument("--rpc-url", required=True, help="Mainnet EVM RPC URL")
    parser.add_argument("--private-key", required=True, help="Deployer EVM Private Key (0x prefixed)")
    parser.add_argument("--netuid", required=True, type=int, help="Target Subnet ID")
    parser.add_argument("--gov-name", required=True, help="Unique name for Governor (e.g., Enigma-Treasury-v1)")
    
    # Financial Limits (Human readable, defaults to recommended limits)
    parser.add_argument("--tao-limit", type=float, default=100.0, help="Max TAO spend per period")
    parser.add_argument("--alpha-limit", type=float, default=10000.0, help="Max Alpha spend per period")
    parser.add_argument("--erc20-limit", type=float, default=10000.0, help="Max ERC20 spend per period")
    
    # Reset Period (Default to 24 hours = 1440 minutes)
    parser.add_argument("--reset-period-min", type=int, default=1440, help="Limit reset period in minutes")

    # Governance & Timing Parameters (Defaults set to standard mainnet)
    parser.add_argument("--min-delay", type=int, default=172800, help="Timelock delay in seconds (default: 48h = 172800)")
    parser.add_argument("--voting-delay", type=int, default=7200, help="Voting delay in blocks (default: 1 day = 7200)")
    parser.add_argument("--voting-period", type=int, default=21600, help="Voting period in blocks (default: 3 days = 21600)")
    parser.add_argument("--proposal-threshold", type=int, default=0, help="Proposal threshold (default: 0)")
    parser.add_argument("--quorum-bps", type=int, default=5000, help="Quorum in basis points (default: 50% = 5000)")
    parser.add_argument("--proposal-expiration", type=int, default=50400, help="Proposal expiration in blocks (default: 7 days = 50400)")

    args = parser.parse_args()

    # Compute paths dynamically
    script_dir = os.path.dirname(os.path.abspath(__file__))
    treasury_dir = os.path.dirname(script_dir)

    # Verify deploy script exists
    deploy_script = Path(treasury_dir) / "scripts" / "deploy.sh"
    if not deploy_script.exists():
        print(f"❌ Error: {deploy_script} not found. Are you in the root directory?")
        sys.exit(1)

    # Get the deployer address from the private key using cast
    print("→ Fetching deployer address...")
    try:
        result = subprocess.run(
            ["cast", "wallet", "address", "--private-key", args.private_key], 
            capture_output=True, text=True, check=True
        )
        deployer_address = result.stdout.strip()
        print(f"  Deployer: {deployer_address}")
    except FileNotFoundError:
        print("❌ Error: Foundry (cast) is not installed or not in PATH.")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"❌ Error validating private key: {e.stderr}")
        sys.exit(1)

    print("\n=== Mainnet Governance Parameters ===")
    print(f"Governor Name:       {args.gov_name}")
    print(f"TAO Limit:           {args.tao_limit} τ")
    print(f"Alpha Limit:         {args.alpha_limit} α")
    print(f"Limit Reset Period:  {args.reset_period_min} minutes")
    print(f"Min Delay:           {args.min_delay} seconds")
    print(f"Voting Delay:        {args.voting_delay} blocks")
    print(f"Voting Period:       {args.voting_period} blocks")
    print(f"Quorum (BPS):        {args.quorum_bps}")
    print(f"Proposal Expiration: {args.proposal_expiration} blocks")
    print("======================================\n")

    # Setup Environment Variables for deploy.sh
    env = os.environ.copy()
    
    # Ensure Foundry is in PATH
    foundry_path = os.path.expanduser("~/.foundry/bin")
    if foundry_path not in env.get("PATH", ""):
        env["PATH"] = foundry_path + ":" + env.get("PATH", "")

    # Core Deployment Setup
    env.update({
        "PRIVATE_KEY": args.private_key,
        "RPC_URL": args.rpc_url,
        "NETUID": str(args.netuid),
        "SKIP_CONFIRMATION": "1",
        "TREASURY_ADMIN": deployer_address,
        "GOV_NAME": args.gov_name,
        
        # --- TIMING & GOVERNANCE PARAMS ---
        "MIN_DELAY": str(args.min_delay),
        "VOTING_DELAY": str(args.voting_delay),
        "VOTING_PERIOD": str(args.voting_period),
        "PROPOSAL_THRESHOLD": str(args.proposal_threshold),
        "QUORUM_BPS": str(args.quorum_bps),
        "PROPOSAL_EXPIRATION": str(args.proposal_expiration),
        
        # --- CIRCUIT BREAKERS ---
        "TAO_LIMIT": to_wei(args.tao_limit),
        "ALPHA_LIMIT": to_wei(args.alpha_limit),
        "ERC20_LIMIT": to_wei(args.erc20_limit),
        "LIMIT_RESET_PERIOD_MIN": str(args.reset_period_min),
    })

    print("🚀 Executing Foundry deployment...")
    
    # Run the bash script from the expected directory
    process = subprocess.run(
        ["bash", "scripts/deploy.sh"], 
        cwd=treasury_dir,
        env=env,
        text=True
    )

    if process.returncode != 0:
        print("\n❌ Deployment failed.")
        sys.exit(process.returncode)

    print("\n✅ Deployment script completed.")

if __name__ == "__main__":
    main()