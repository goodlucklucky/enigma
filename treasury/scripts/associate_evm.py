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

import argparse
import sys
from pathlib import Path

current_dir = Path(__file__).resolve().parent
if str(current_dir) not in sys.path:
    sys.path.append(str(current_dir))

from utils.common import add_web3_arguments, setup_web3_with_account

try:
    from substrateinterface import SubstrateInterface, Keypair
    from eth_account.messages import encode_defunct
except ImportError:
    sys.exit("Please install: pip install substrate-interface eth-account")


def main():
    parser = argparse.ArgumentParser(description="Associate EVM address with Bittensor hotkey")
    parser.add_argument("--hotkey", required=True, help="Hotkey SS58 address or 0x hex (32 bytes)")

    # Mutually exclusive group so the user provides exactly one authentication method
    auth_group = parser.add_mutually_exclusive_group(required=True)
    auth_group.add_argument("--hotkey-seed", help="Hotkey seed phrase")
    auth_group.add_argument("--hotkey-private-key", help="Hotkey private key / raw seed (0x hex)")

    parser.add_argument("--netuid", type=int, required=True, help="The subnet netuid")
    parser.add_argument("--fast-mode", action="store_true", help="Apply fixes for fast localnet block production")

    add_web3_arguments(parser)
    args = parser.parse_args()

    # === 1. PARSE AND VALIDATE HOTKEY ===
    # Get raw bytes from the --hotkey argument
    if args.hotkey.startswith(("0x", "0X")):
        hotkey_bytes = bytes.fromhex(args.hotkey[2:])
    else:
        hotkey_kp = Keypair(ss58_address=args.hotkey)
        hotkey_bytes = hotkey_kp.public_key

    # Create keypair from the provided seed or private key
    if args.hotkey_private_key:
        clean_hex = args.hotkey_private_key.replace("0x", "").replace("0X", "")
        key_bytes = bytes.fromhex(clean_hex)

        if len(key_bytes) == 32:
            # 32-byte seed (Maps to "secretSeed" in Polkadot/Substrate JSON)
            hotkey_keypair = Keypair.create_from_seed(seed_hex=key_bytes)
        elif len(key_bytes) == 64:
            # 64-byte expanded private key (Maps to "privateKey" in Polkadot/Substrate JSON)
            # We initialize it directly and pass the hotkey_bytes as the public key
            hotkey_keypair = Keypair(
                private_key=key_bytes,
                public_key=hotkey_bytes,
                ss58_format=42
            )
        else:
            print(f"\n❌ ERROR: Invalid key length!")
            print(f"   Expected 32 bytes (secret seed) or 64 bytes (private key).")
            print(f"   You provided {len(key_bytes)} bytes.")
            sys.exit(1)
    else:
        # Parse the mnemonic phrase
        hotkey_keypair = Keypair.create_from_uri(args.hotkey_seed)

    # SAFETY CHECK: Ensure the seed/key matches the target hotkey
    if hotkey_keypair.public_key != hotkey_bytes:
        print(f"\n❌ ERROR: Security validation failed!")
        print(f"   The provided secret/seed derives a different public key")
        print(f"   than the target --hotkey: {args.hotkey}")
        sys.exit(1)

    # === 2. SETUP EVM & CHAIN ===
    # Setup Web3 for EVM signing
    w3, evm_account = setup_web3_with_account(args)
    evm_address = evm_account.address

    print(f"{'=' * 60}")
    print(f"EVM ASSOCIATION")
    print(f"{'=' * 60}")
    print(f"Hotkey (Signer): {args.hotkey}")
    print(f"Netuid:          {args.netuid}")
    print(f"EVM:             {evm_address}")
    print(f"Fast Mode:       {args.fast_mode}")
    print(f"{'=' * 60}")

    substrate_url = args.rpc_url.replace("http://", "ws://").replace("https://", "wss://")
    substrate = SubstrateInterface(url=substrate_url)

    # We MUST use the exact current block for the signature to validate
    block_number = substrate.get_block_number(substrate.get_chain_head())
    print(f"Targeting block: {block_number}")

    # === 3. MESSAGE CONSTRUCTION & EVM SIGNATURE ===
    # block_number hashed (matching Rust pallet)
    block_bytes = block_number.to_bytes(8, byteorder='little')
    block_hash = w3.keccak(block_bytes)

    raw_message = hotkey_bytes + block_hash
    print(f"Raw message (hex): {raw_message.hex()}")

    # Sign with EVM key
    signed = evm_account.sign_message(encode_defunct(primitive=raw_message))
    signature = signed.signature.hex()
    if not signature.startswith("0x"):
        signature = "0x" + signature

    # === 4. SUBMIT EXTRINSIC ===
    print(f"\nSubmitting associate_evm_key extrinsic as HOTKEY...")

    call = substrate.compose_call(
        call_module="SubtensorModule",
        call_function="associate_evm_key",
        call_params={
            'netuid': args.netuid,
            'evm_key': evm_address,
            'block_number': block_number,
            'signature': signature
        }
    )

    if args.fast_mode:
        print("Fast mode: Extending extrinsic era to 128 blocks.")
        extrinsic = substrate.create_signed_extrinsic(call=call, keypair=hotkey_keypair, era={'period': 128})
    else:
        extrinsic = substrate.create_signed_extrinsic(call=call, keypair=hotkey_keypair)

    receipt = substrate.submit_extrinsic(extrinsic, wait_for_inclusion=True)

    if receipt.is_success:
        print(f"\n✅ EVM Association successful!")
        print(f"   Block: {receipt.block_hash}")
        print(f"   EVM {evm_address} → Hotkey {args.hotkey}")
    else:
        print(f"\n❌ Association failed: {receipt.error_message}")
        sys.exit(1)


if __name__ == "__main__":
    main()
